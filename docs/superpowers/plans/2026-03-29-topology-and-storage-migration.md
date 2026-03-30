# Topology Enhancement & Monitoring Storage Migration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan
> task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add panel-level entity mappings to the topology WebSocket response (eliminating fragile pattern matching), migrate global monitoring settings from
config entry options to the storage layer, and add a `set_global_monitoring` service.

**Architecture:** Extend the existing `panel_topology` WebSocket handler to include a `panel_entities` section resolved via unique_id lookups. Move global
monitoring config into the same `Store` file that already holds per-circuit overrides. Add a new service for updating global settings. Remove the config flow
monitoring options step.

**Tech Stack:** Python, Home Assistant APIs (entity registry, storage, services, WebSocket)

**Repo:** `/Users/bflood/projects/HA/span`

**Tests:** `.venv/bin/python -m pytest tests/ -q`

---

## Task 1: Add panel_entities to topology WebSocket response

**Files:**

- Modify: `custom_components/span_panel/websocket.py`
- Create: `tests/test_topology_panel_entities.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_topology_panel_entities.py
"""Tests for panel_entities section in topology response."""

import pytest
from unittest.mock import MagicMock, patch

from tests.factories import SpanPanelSnapshotFactory


class TestPanelEntitiesInTopology:
    """Verify panel_entities maps sensor keys to entity_ids."""

    def test_panel_entities_resolves_known_sensors(self):
        """Panel entities section contains resolved entity IDs."""
        from custom_components.span_panel.websocket import (
            _build_panel_entity_map,
        )
        from custom_components.span_panel.helpers import (
            build_panel_unique_id,
        )

        serial = "test-serial-123"

        # Mock entity registry that resolves unique_ids to entity_ids
        mock_registry = MagicMock()

        def mock_get_entity_id(domain, integration, unique_id):
            mapping = {
                build_panel_unique_id(serial, "instantGridPowerW"):
                    "sensor.my_renamed_current_power",
                build_panel_unique_id(serial, "sitePowerW"):
                    "sensor.custom_site_power",
                build_panel_unique_id(serial, "dsm_state"):
                    "sensor.my_grid_state",
            }
            return mapping.get(unique_id)

        mock_registry.async_get_entity_id = mock_get_entity_id

        result = _build_panel_entity_map(serial, mock_registry)

        assert result["current_power"] == "sensor.my_renamed_current_power"
        assert result["site_power"] == "sensor.custom_site_power"
        assert result["dsm_state"] == "sensor.my_grid_state"
        # Unresolved entries should be absent
        assert "pv_power" not in result

    def test_panel_entities_empty_when_no_entities_found(self):
        """Panel entities is empty dict when no entities resolve."""
        from custom_components.span_panel.websocket import (
            _build_panel_entity_map,
        )

        mock_registry = MagicMock()
        mock_registry.async_get_entity_id = MagicMock(return_value=None)

        result = _build_panel_entity_map("unknown-serial", mock_registry)

        assert result == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_topology_panel_entities.py -v`

Expected: FAIL — `_build_panel_entity_map` does not exist.

- [ ] **Step 3: Implement `_build_panel_entity_map`**

In `custom_components/span_panel/websocket.py`, add:

```python
from .helpers import build_panel_unique_id

# Panel-level sensor keys to resolve via entity registry.
# Maps topology role name -> sensor definition key (used by
# build_panel_unique_id to construct the unique_id).
_PANEL_SENSOR_KEYS: dict[str, str] = {
    "current_power": "instantGridPowerW",
    "site_power": "sitePowerW",
    "grid_power": "gridPowerFlowW",
    "feedthrough_power": "feedthroughPowerW",
    "pv_power": "pvPowerW",
    "battery_power": "batteryPowerW",
    "battery_level": "storage_battery_percentage",
    "dsm_state": "dsm_state",
    "main_breaker_rating": "main_breaker_rating",
    "upstream_l1_current": "upstream_l1_current",
    "upstream_l2_current": "upstream_l2_current",
    "downstream_l1_current": "downstream_l1_current",
    "downstream_l2_current": "downstream_l2_current",
    "l1_voltage": "l1_voltage",
    "l2_voltage": "l2_voltage",
}


def _build_panel_entity_map(
    serial: str,
    entity_registry: er.EntityRegistry,
) -> dict[str, str]:
    """Resolve panel-level sensor unique_ids to current entity_ids.

    Returns a dict of {role: entity_id} for sensors that exist in the
    registry. Entries that cannot be resolved are omitted.
    """
    result: dict[str, str] = {}
    for role, description_key in _PANEL_SENSOR_KEYS.items():
        unique_id = build_panel_unique_id(serial, description_key)
        entity_id = entity_registry.async_get_entity_id(
            "sensor", DOMAIN, unique_id
        )
        if entity_id is not None:
            result[role] = entity_id
    return result
```

- [ ] **Step 4: Add panel_entities to the topology response**

In `handle_panel_topology`, after building the entity_map and before `connection.send_result`, add:

```python
panel_entities = _build_panel_entity_map(
    snapshot.serial_number, entity_registry
)
```

And include it in the response dict:

```python
connection.send_result(
    msg["id"],
    {
        "serial": snapshot.serial_number,
        "firmware": snapshot.firmware_version,
        "panel_size": snapshot.panel_size,
        "device_id": device_id,
        "device_name": device_entry.name,
        "panel_entities": panel_entities,
        "circuits": circuits,
        "sub_devices": sub_devices,
    },
)
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_topology_panel_entities.py tests/ -q`

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add custom_components/span_panel/websocket.py \
  tests/test_topology_panel_entities.py
git commit -m "feat: add panel_entities to topology response for rename-safe entity lookup"
```

---

## Task 2: Migrate global monitoring settings to storage

**Files:**

- Modify: `custom_components/span_panel/current_monitor.py`
- Modify: `tests/test_current_monitor.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_current_monitor.py

class TestGlobalSettingsStorage:
    """Tests for global monitoring settings in storage."""

    def test_load_global_settings_from_storage(self):
        """Monitor loads global settings from storage when available."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options())
        # Simulate stored global settings
        monitor._store_data = {
            "global": {
                "continuous_threshold_pct": 70,
                "spike_threshold_pct": 90,
                "window_duration_m": 10,
                "cooldown_duration_m": 20,
            },
            "circuit_overrides": {},
            "mains_overrides": {},
        }
        settings = monitor.get_global_settings()
        assert settings["continuous_threshold_pct"] == 70
        assert settings["spike_threshold_pct"] == 90

    def test_global_settings_fallback_to_options(self):
        """Monitor falls back to entry.options when no stored globals."""
        hass = _make_hass()
        options = _make_options()
        options[CONTINUOUS_THRESHOLD_PCT] = 85
        monitor = _make_monitor(hass, options)
        # No "global" key in storage
        monitor._store_data = {
            "circuit_overrides": {},
            "mains_overrides": {},
        }
        settings = monitor.get_global_settings()
        assert settings["continuous_threshold_pct"] == 85

    def test_set_global_settings_persists_to_storage(self):
        """Setting global settings writes to storage."""
        hass = _make_hass()
        monitor = _make_monitor(hass, _make_options())
        monitor._store_data = {
            "circuit_overrides": {},
            "mains_overrides": {},
        }
        monitor.set_global_settings({
            "continuous_threshold_pct": 75,
            "spike_threshold_pct": 95,
            "window_duration_m": 20,
            "cooldown_duration_m": 30,
        })
        assert monitor._store_data["global"]["continuous_threshold_pct"] == 75
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_current_monitor.py::TestGlobalSettingsStorage -v`

Expected: FAIL — `get_global_settings` and `set_global_settings` don't exist.

- [ ] **Step 3: Implement global settings in CurrentMonitor**

Add to `current_monitor.py`:

```python
def get_global_settings(self) -> dict[str, Any]:
    """Get the effective global monitoring settings.

    Returns stored global settings if available, otherwise falls
    back to config entry options.
    """
    stored = self._store_data.get("global")
    if stored:
        return {
            "continuous_threshold_pct": stored.get(
                "continuous_threshold_pct",
                DEFAULT_CONTINUOUS_THRESHOLD_PCT,
            ),
            "spike_threshold_pct": stored.get(
                "spike_threshold_pct",
                DEFAULT_SPIKE_THRESHOLD_PCT,
            ),
            "window_duration_m": stored.get(
                "window_duration_m",
                DEFAULT_WINDOW_DURATION_M,
            ),
            "cooldown_duration_m": stored.get(
                "cooldown_duration_m",
                DEFAULT_COOLDOWN_DURATION_M,
            ),
            "notify_targets": stored.get("notify_targets", "notify.notify"),
            "enable_persistent_notifications": stored.get(
                "enable_persistent_notifications", True
            ),
            "enable_event_bus": stored.get("enable_event_bus", True),
        }

    # Fallback to config entry options
    opts = self._entry.options
    return {
        "continuous_threshold_pct": opts.get(
            CONTINUOUS_THRESHOLD_PCT, DEFAULT_CONTINUOUS_THRESHOLD_PCT
        ),
        "spike_threshold_pct": opts.get(
            SPIKE_THRESHOLD_PCT, DEFAULT_SPIKE_THRESHOLD_PCT
        ),
        "window_duration_m": opts.get(
            WINDOW_DURATION_M, DEFAULT_WINDOW_DURATION_M
        ),
        "cooldown_duration_m": opts.get(
            COOLDOWN_DURATION_M, DEFAULT_COOLDOWN_DURATION_M
        ),
        "notify_targets": opts.get(NOTIFY_TARGETS, "notify.notify"),
        "enable_persistent_notifications": opts.get(
            ENABLE_PERSISTENT_NOTIFICATIONS, True
        ),
        "enable_event_bus": opts.get(ENABLE_EVENT_BUS, True),
    }

def set_global_settings(self, settings: dict[str, Any]) -> None:
    """Update global monitoring settings in storage."""
    self._store_data.setdefault("global", {})
    for key in (
        "continuous_threshold_pct",
        "spike_threshold_pct",
        "window_duration_m",
        "cooldown_duration_m",
        "notify_targets",
        "enable_persistent_notifications",
        "enable_event_bus",
    ):
        if key in settings:
            self._store_data["global"][key] = settings[key]
    self._hass.async_create_task(self.async_save_overrides())
```

Also update the threshold evaluation methods to read from `get_global_settings()` instead of `self._entry.options` directly.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_current_monitor.py -q`

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/span_panel/current_monitor.py \
  tests/test_current_monitor.py
git commit -m "feat: migrate global monitoring settings to storage layer"
```

---

## Task 3: Add set_global_monitoring service

**Files:**

- Modify: `custom_components/span_panel/__init__.py`
- Modify: `custom_components/span_panel/services.yaml`
- Modify: `custom_components/span_panel/strings.json`
- Modify: `custom_components/span_panel/translations/en.json`
- Create: `tests/test_global_monitoring_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_global_monitoring_service.py
"""Tests for set_global_monitoring service."""

import pytest
import voluptuous as vol


class TestSetGlobalMonitoringSchema:
    """Tests for service schema validation."""

    def test_schema_accepts_valid_input(self):
        """Service schema accepts valid global monitoring input."""
        from custom_components.span_panel.__init__ import (
            _build_set_global_monitoring_schema,
        )

        schema = _build_set_global_monitoring_schema()
        result = schema({
            "continuous_threshold_pct": 75,
            "spike_threshold_pct": 95,
            "window_duration_m": 20,
            "cooldown_duration_m": 30,
            "notify_targets": "notify.mobile_app",
            "enable_persistent_notifications": False,
            "enable_event_bus": True,
        })
        assert result["continuous_threshold_pct"] == 75

    def test_schema_accepts_partial_input(self):
        """Service schema accepts partial input (only some fields)."""
        from custom_components.span_panel.__init__ import (
            _build_set_global_monitoring_schema,
        )

        schema = _build_set_global_monitoring_schema()
        result = schema({"continuous_threshold_pct": 70})
        assert result["continuous_threshold_pct"] == 70
        assert "spike_threshold_pct" not in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_global_monitoring_service.py -v`

Expected: FAIL — `_build_set_global_monitoring_schema` does not exist.

- [ ] **Step 3: Implement the service**

In `__init__.py`, add the schema builder and handler:

```python
def _build_set_global_monitoring_schema() -> vol.Schema:
    """Build schema for set_global_monitoring service."""
    return vol.Schema(
        {
            vol.Optional("continuous_threshold_pct"): vol.All(
                int, vol.Range(min=1, max=200)
            ),
            vol.Optional("spike_threshold_pct"): vol.All(
                int, vol.Range(min=1, max=200)
            ),
            vol.Optional("window_duration_m"): vol.All(
                int, vol.Range(min=1, max=180)
            ),
            vol.Optional("cooldown_duration_m"): vol.All(
                int, vol.Range(min=1, max=180)
            ),
            vol.Optional("notify_targets"): str,
            vol.Optional("enable_persistent_notifications"): bool,
            vol.Optional("enable_event_bus"): bool,
        }
    )
```

Register the service in `_async_register_monitoring_services`:

```python
async def handle_set_global_monitoring(call: ServiceCall) -> None:
    """Handle set_global_monitoring service call."""
    monitor = _get_monitor(hass)
    if monitor is None:
        raise ServiceValidationError(
            "Current monitoring is not enabled"
        )
    monitor.set_global_settings(dict(call.data))

hass.services.async_register(
    DOMAIN,
    "set_global_monitoring",
    handle_set_global_monitoring,
    schema=_build_set_global_monitoring_schema(),
)
```

- [ ] **Step 4: Add service definition to services.yaml**

```yaml
set_global_monitoring:
  fields:
    continuous_threshold_pct:
      selector:
        number:
          min: 1
          max: 200
          unit_of_measurement: "%"
    spike_threshold_pct:
      selector:
        number:
          min: 1
          max: 200
          unit_of_measurement: "%"
    window_duration_m:
      selector:
        number:
          min: 1
          max: 180
          unit_of_measurement: min
    cooldown_duration_m:
      selector:
        number:
          min: 1
          max: 180
          unit_of_measurement: min
    notify_targets:
      selector:
        text:
    enable_persistent_notifications:
      selector:
        boolean:
    enable_event_bus:
      selector:
        boolean:
```

- [ ] **Step 5: Add translations**

Add to both `strings.json` and `translations/en.json` under `services.set_global_monitoring`.

- [ ] **Step 6: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -q`

Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add custom_components/span_panel/__init__.py \
  custom_components/span_panel/services.yaml \
  custom_components/span_panel/strings.json \
  custom_components/span_panel/translations/en.json \
  tests/test_global_monitoring_service.py
git commit -m "feat: add set_global_monitoring service for panel UI control"
```

---

## Task 4: Remove config flow monitoring options step

**Files:**

- Modify: `custom_components/span_panel/config_flow.py`
- Modify: `custom_components/span_panel/config_flow_options.py`
- Modify: `tests/test_config_flow.py` (if monitoring step tests exist)

- [ ] **Step 1: Remove monitoring_options and monitoring_settings steps**

In `config_flow.py`, the options flow currently has a menu with "general_options" and "monitoring_options". Remove the monitoring menu option — the options flow
only shows general options. If only one menu option remains, the menu can be replaced with a direct step.

In `config_flow_options.py`, remove `build_monitoring_settings_schema` and `get_monitoring_settings_defaults` (no longer needed).

- [ ] **Step 2: Update tests**

Remove or update any tests that assert the monitoring options step exists in the config flow. Add a test verifying the options flow no longer offers a
monitoring menu option.

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/ -q`

Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add custom_components/span_panel/config_flow.py \
  custom_components/span_panel/config_flow_options.py \
  tests/test_config_flow.py
git commit -m "refactor: remove monitoring options from config flow (managed via services)"
```

---

## Task 5: Update span-card to consume panel_entities

**Files:**

- Modify: `/Users/bflood/projects/HA/cards/span-card/src/core/dom-updater.js`
- Modify: `/Users/bflood/projects/HA/cards/span-card/src/core/header-renderer.js`

- [ ] **Step 1: Update dom-updater.js**

Replace all calls to `_findPanelEntity(hass, topology, suffix)` with direct lookups from `topology.panel_entities`:

```js
// Replace:
const panelPowerEntity = _findPanelEntity(hass, topology, "current_power");
// With:
const panelPowerEntity = topology.panel_entities?.current_power;
```

Apply this for all panel entity lookups (site_power, current_power, feedthrough_power, dsm_state, pv_power, battery_level).

Remove the `_findPanelEntity` function entirely.

- [ ] **Step 2: Update header-renderer.js**

Replace the `_hasPanelEntity(hass, suffix)` helper with a check against `topology.panel_entities`:

```js
// Replace:
${_hasPanelEntity(hass, topology, "site_power") ? `...` : ""}
// With:
${topology.panel_entities?.site_power ? `...` : ""}
```

Remove the `_hasPanelEntity` and `_hasSolarEntity` helpers. The `hass` parameter can be removed from `buildHeaderHTML` since entity existence is now determined
from the topology.

Update call sites if the `hass` parameter is removed.

- [ ] **Step 3: Build and commit**

```bash
cd /Users/bflood/projects/HA/cards/span-card && npm run build
git add src/core/dom-updater.js src/core/header-renderer.js \
  src/card/span-panel-card.js src/panel/tab-dashboard.js
git commit -m "feat: use topology panel_entities instead of pattern matching"
```
