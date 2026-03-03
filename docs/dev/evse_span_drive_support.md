# EVSE (SPAN Drive) Entity Support

## Context

Issue [#153](https://github.com/SpanPanel/span/issues/153) requests SPAN Drive EV charger status in the integration. Previously blocked because the v1 REST API
exposed no EVSE data. The v2 eBus/MQTT API now exposes a full `energy.ebus.device.evse` Homie node with 9 properties. All are read-only.

### eBus EVSE Schema

```yaml
energy.ebus.device.evse:
  vendor-name:        string
  product-name:       string
  part-number:        string
  serial-number:      string
  software-version:   string
  feed:               enum        # Circuit ID the EVSE is connected to
  lock-state:         enum        # UNKNOWN | LOCKED | UNLOCKED
  status:             enum        # OCPP-based charger status (10 values)
  advertised-current: float (A)   # Current being offered to the EV
```

**Status enum values** (OCPP-derived): `UNKNOWN`, `AVAILABLE`, `PREPARING`, `CHARGING`, `SUSPENDED_EV`, `SUSPENDED_EVSE`, `FINISHING`, `RESERVED`, `FAULTED`,
`UNAVAILABLE`

**None of these properties have `settable: true`** — we cannot control charge rate or lock state via eBus. The only controllable properties are on the circuit
node itself (relay, shed-priority), which we already expose.

### Current State

The library (`span-panel-api`) extracts only `feed` and `relative-position` from EVSE nodes to annotate circuits with `device_type="evse"`. All other EVSE
properties are parsed by MQTT but discarded during snapshot building. The integration creates breaker switches and shed priority selects for EVSE circuits;
standard circuit sensors (power, energy) already exist.

---

## Design: Beyond span-hass

The [span-hass](https://github.com/electrification-bus/span-hass) reference implementation creates 9 sensor entities per EVSE (status, lock-state,
advertised-current, plus 5 diagnostic string sensors for vendor/product/serial/version/part-number, plus feed circuit). All are plain string sensors under a
generic "EV Charger" sub-device.

This design improves on that approach:

| Feature                | span-hass                   | This design                                                        |
| ---------------------- | --------------------------- | ------------------------------------------------------------------ |
| EVSE metadata          | 5 diagnostic string sensors | HA DeviceInfo attributes (manufacturer, model, serial, sw_version) |
| Charger status         | Plain string sensor         | `SensorDeviceClass.ENUM` with translated state names               |
| Lock state             | Plain string sensor         | Enum sensor with translations                                      |
| Derived binary sensors | None                        | Charging (`BATTERY_CHARGING`), EV Connected (`PLUG`)               |
| Translations           | English only                | 5 languages (en, es, fr, ja, pt)                                   |
| EVSE circuit naming    | Generic                     | "EV Charger" fallback name for unnamed circuits                    |
| Simulation support     | None                        | EVSE entities in simulator mode                                    |
| Multiple EVSE support  | Single node                 | `dict[str, SpanEvseSnapshot]` keyed by node_id                     |

### Architecture: Dual-Node Design

EVSE has two representations in the Homie description:

1. **Metadata node** (`energy.ebus.device.evse`): Device info, charger status, lock state, advertised current. References a circuit via `feed`.
2. **Circuit node** (physical breaker): Power, energy, relay state, shed priority.

Power and energy flow through the circuit node. The EVSE metadata node adds charger-specific state. This means EVSE entities live on a **sub-device** (the
charger), while circuit power/energy entities remain on the panel device as regular circuit sensors.

### Sub-Device with Rich DeviceInfo

Instead of creating 5 diagnostic entities, we map EVSE metadata into HA's DeviceInfo:

```python
DeviceInfo(
    identifiers={(DOMAIN, f"{panel_id}_evse_{evse_node_id}")},
    name=product_name or "EV Charger",
    manufacturer=vendor_name or "SPAN",
    model=product_name or "SPAN Drive",
    serial_number=serial_number,
    sw_version=software_version,
    via_device=(DOMAIN, panel_identifier),  # links to parent panel
)
```

The HA device page shows manufacturer, model, serial, and firmware natively. `via_device` creates the parent-child hierarchy in the device registry.
`part_number` goes into `extra_state_attributes` on the status sensor since DeviceInfo has no part_number field.

---

## Entities Per EVSE Device

| Entity             | Platform      | Device Class       | State Class   | Icon             |
| ------------------ | ------------- | ------------------ | ------------- | ---------------- |
| Charger Status     | sensor        | `ENUM`             | —             | `mdi:ev-station` |
| Advertised Current | sensor        | `CURRENT`          | `MEASUREMENT` | —                |
| Lock State         | sensor        | `ENUM`             | —             | `mdi:lock`       |
| Charging           | binary_sensor | `BATTERY_CHARGING` | —             | —                |
| EV Connected       | binary_sensor | `PLUG`             | —             | —                |

**5 entities** per EVSE, compared to span-hass's 9.

### Derived Binary Sensors

These provide high-value automation triggers that span-hass does not offer:

**Charging** — ON when `status == "CHARGING"`. Device class `BATTERY_CHARGING` gives HA native "Charging" / "Not charging" display. Useful for automations like
"notify when car finishes charging" or "shift loads while EV charges."

**EV Connected** — ON when `status` is in `{PREPARING, CHARGING, SUSPENDED_EV, SUSPENDED_EVSE, FINISHING}`. Device class `PLUG` gives HA native "Plugged in" /
"Unplugged" display. Useful for "remind me to plug in the car" automations.

### Charger Status Extra Attributes

The status sensor includes additional context as extra_state_attributes:

- `advertised_current_a` — current being offered (amps)
- `lock_state` — connector lock state
- `part_number` — EVSE part number (no DeviceInfo field for this)
- `feed_circuit_id` — which circuit the EVSE is connected to

This gives users a single entity to inspect for full charger state.

---

## Data Flow

```text
MQTT: ebus/5/{serial}/evse/{property}
  |
  v
HomieDeviceConsumer._handle_property()
  |
  v
_build_evse_devices()          # NEW — iterates all TYPE_EVSE nodes
  |                             # extracts all 9 properties per node
  v
dict[str, SpanEvseSnapshot]    # keyed by Homie node_id
  |
  v
SpanPanelSnapshot.evse         # NEW field on the snapshot model
  |
  v
SpanPanelCoordinator           # auto-detect "evse" capability
  |                             # trigger reload when EVSE first appears
  v
sensor platform                # SpanEvseSensor entities (status, current, lock)
binary_sensor platform         # SpanEvseBinarySensor entities (charging, connected)
```

The existing `_build_feed_metadata()` continues to annotate circuits with `device_type="evse"` — this is unchanged. The new `_build_evse_devices()` runs
alongside it, capturing the metadata that was previously discarded.

---

## Implementation: Library (`span-panel-api`)

### models.py

New `SpanEvseSnapshot` dataclass after `SpanPVSnapshot`:

```python
@dataclass(frozen=True, slots=True)
class SpanEvseSnapshot:
    """EV Charger (EVSE) state — populated when EVSE node is commissioned."""

    node_id: str                              # Homie node ID
    feed_circuit_id: str                      # Normalized circuit ID
    status: str = "UNKNOWN"                   # OCPP charger status
    lock_state: str = "UNKNOWN"               # LOCKED | UNLOCKED | UNKNOWN
    advertised_current_a: float | None = None # Amps offered to EV
    vendor_name: str | None = None
    product_name: str | None = None
    part_number: str | None = None
    serial_number: str | None = None
    software_version: str | None = None
```

Add to `SpanPanelSnapshot`:

```python
evse: dict[str, SpanEvseSnapshot] = field(default_factory=dict)
```

### mqtt/homie.py

New method `_build_evse_devices()` — iterates all `TYPE_EVSE` nodes, extracts all 9 properties, returns `dict[str, SpanEvseSnapshot]`. Called from
`build_snapshot()` alongside `_build_battery()` and `_build_pv()`.

### simulation.py

Generate EVSE snapshot entries for bidirectional circuits (existing `device_type == "evse"` detection). Simulated EVSE shows `CHARGING` when power > 100W,
`AVAILABLE` otherwise. Provides realistic EVSE data for development/testing without hardware.

### **init**.py

Export `SpanEvseSnapshot` from the library's public API.

---

## Implementation: Integration (`span`)

### util.py — `evse_device_info()`

New helper creating `DeviceInfo` with `via_device` linking to the parent panel. Maps vendor/product/serial/version from EVSE metadata into native DeviceInfo
fields.

### sensor_definitions.py

New `SpanEvseSensorEntityDescription` dataclass with `value_fn: Callable[[SpanEvseSnapshot], ...]`. Three sensor definitions:

1. **evse_status** — `SensorDeviceClass.ENUM`, 10 options
2. **evse_advertised_current** — `SensorDeviceClass.CURRENT`, `SensorStateClass.MEASUREMENT`
3. **evse_lock_state** — `SensorDeviceClass.ENUM`, 3 options

### sensors/evse.py (new file)

`SpanEvseSensor` extends `SpanSensorBase[SpanEvseSensorEntityDescription, SpanEvseSnapshot]`. Overrides `_attr_device_info` to use EVSE sub-device. Each sensor
instance holds an `_evse_id` referencing a specific EVSE in the snapshot dict.

Unique ID pattern: `span_{serial}_evse_{node_id}_{key}`

### binary_sensor.py

New `SpanEvseBinarySensorEntityDescription` and `SpanEvseBinarySensor` class. Two descriptions:

1. **evse_charging** — `BinarySensorDeviceClass.BATTERY_CHARGING`
2. **evse_ev_connected** — `BinarySensorDeviceClass.PLUG`

Created conditionally in `async_setup_entry()` when `snapshot.evse` is non-empty.

### sensors/factory.py

- `has_evse(snapshot)` — `len(snapshot.evse) > 0`
- `create_evse_sensors(coordinator, snapshot)` — iterates all EVSE devices and descriptions
- Update `detect_capabilities()` to include `"evse"`
- Update `create_native_sensors()` to call `create_evse_sensors()`

### coordinator.py

Add EVSE detection to `_detect_capabilities()`:

```python
if any(c.device_type == "evse" for c in snapshot.circuits.values()) or len(snapshot.evse) > 0:
    caps.add("evse")
```

### sensors/circuit.py

Add "EV Charger" as fallback name for unnamed EVSE circuits, matching the existing "Solar" pattern for PV circuits.

### strings.json + translations

Entity names and enum state translations in all 6 language files. Status states get human-readable translations:

| Status       | English              | Spanish                 | French                | Japanese       | Portuguese            |
| ------------ | -------------------- | ----------------------- | --------------------- | -------------- | --------------------- |
| CHARGING     | Charging             | Cargando                | En charge             | 充電中         | Carregando            |
| AVAILABLE    | Available            | Disponible              | Disponible            | 利用可能       | Disponivel            |
| PREPARING    | Preparing            | Preparando              | Preparation           | 準備中         | Preparando            |
| SUSPENDED_EV | Suspended by Vehicle | Suspendido por Vehiculo | Suspendu par Vehicule | 車両による中断 | Suspenso pelo Veiculo |
| FAULTED      | Faulted              | En fallo                | En panne              | 故障           | Com falha             |

(Complete translations for all 10 status values + 3 lock states in all 5 languages.)

---

## Edge Cases

| Case                     | Behavior                                                  |
| ------------------------ | --------------------------------------------------------- |
| No EVSE commissioned     | `snapshot.evse` is empty dict; no entities created        |
| EVSE appears after setup | Capability detection sees new "evse" cap, triggers reload |
| Multiple EVSE devices    | Each gets its own sub-device + full entity set            |
| EVSE removed from panel  | Entities become unavailable (standard HA behavior)        |
| Missing metadata fields  | DeviceInfo uses fallback values ("SPAN", "SPAN Drive")    |
| Simulation mode          | Bidirectional circuits generate simulated EVSE snapshots  |
| EVSE without feed        | Skipped — `feed` is required to associate with a circuit  |

---

## Files Modified

### Library (`span-panel-api`)

| File                               | Change                                                    |
| ---------------------------------- | --------------------------------------------------------- |
| `src/span_panel_api/models.py`     | Add `SpanEvseSnapshot`, add `evse` to `SpanPanelSnapshot` |
| `src/span_panel_api/mqtt/homie.py` | Add `_build_evse_devices()`, call from `build_snapshot()` |
| `src/span_panel_api/simulation.py` | Generate EVSE entries for bidirectional circuits          |
| `src/span_panel_api/__init__.py`   | Export `SpanEvseSnapshot`                                 |
| `tests/test_mqtt_homie.py`         | EVSE parsing tests                                        |

### Integration (`span`)

| File                                                 | Change                                                         |
| ---------------------------------------------------- | -------------------------------------------------------------- |
| `custom_components/span_panel/util.py`               | Add `evse_device_info()`                                       |
| `custom_components/span_panel/sensor_definitions.py` | Add EVSE sensor descriptions                                   |
| `custom_components/span_panel/sensors/evse.py`       | **New** — EVSE sensor entity class                             |
| `custom_components/span_panel/sensors/factory.py`    | Add `has_evse()`, `create_evse_sensors()`, update capabilities |
| `custom_components/span_panel/binary_sensor.py`      | Add EVSE binary sensor descriptions + class                    |
| `custom_components/span_panel/coordinator.py`        | Add "evse" to `_detect_capabilities()`                         |
| `custom_components/span_panel/sensors/circuit.py`    | "EV Charger" fallback name                                     |
| `custom_components/span_panel/strings.json`          | EVSE entity names + enum states                                |
| `custom_components/span_panel/translations/*.json`   | 5 translation files                                            |
| `tests/test_evse_entities.py`                        | **New** — EVSE entity tests                                    |

---

## Verification

1. `cd /Users/bflood/projects/HA/span-panel-api && python -m pytest tests/ -q` — library tests pass
2. `cd /Users/bflood/projects/HA/span && python -m pytest tests/ -q` — integration tests pass
3. Simulator mode with bidirectional circuit creates EVSE entities
4. EVSE sub-device in HA device registry shows manufacturer/model/serial/sw_version
5. Charger Status shows translated enum states
6. Binary sensors derive correctly from charger status
7. Capability detection triggers reload when EVSE first appears
