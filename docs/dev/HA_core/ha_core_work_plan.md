# Home Assistant Core Submission Plan

## Overview

This document outlines the work required to submit the SPAN Panel integration to Home Assistant core. The analysis compares the current custom integration
against HA's [Integration Quality Scale][quality-scale] and [developer documentation][dev-docs].

[quality-scale]: https://developers.home-assistant.io/docs/core/integration-quality-scale/
[dev-docs]: https://developers.home-assistant.io/docs/development_index/

## Current Alignment

The integration already satisfies several core requirements:

- Config flow with zeroconf discovery and reauth
- Coordinator pattern in `coordinator.py`
- Consistent entity unique IDs
- `has_entity_name = True` on all entities
- Push streaming (`local_push` IoT class)
- Device info construction
- Translations in `strings.json`
- Strict typing (mypy strict, pyright)
- Config version migration (v1 through v5)
- Good test coverage (41 test files)

---

## Phase 1: Strip Non-Core Features

Core integrations communicate with real devices only. Features that exist purely for development convenience, direct database manipulation, or custom UX
patterns not supported by core must be removed from the integration destined for core submission.

> **Note on simulation:** Simulation will be reimplemented as an eBus-level simulator external to the integration. From the integration's perspective it will
> always be talking to a real device (or something indistinguishable from one). No simulation awareness needs to exist in the integration code.

### 1.1 Remove Simulation Mode

Remove all simulation-related code paths and files:

- `simulation_factory.py`
- `simulation_generator.py`
- `simulation_utils.py`
- `simulation_configs/` directory
- `config_flow_utils/simulation.py`
- Simulation branches in `config_flow.py` (`simulator_config` step, simulation serial generation, simulation start time handling)
- Simulation branches in `coordinator.py` (polling path for `DynamicSimulationEngine`, offline simulation minutes)
- Simulation-related constants (`CONF_SIMULATION_CONFIG`, `CONF_SIMULATION_START_TIME`, `CONF_SIMULATION_OFFLINE_MINUTES`)
- Simulation-related options in `options.py`

### 1.2 Flatten Directory Structure

Core integrations use a flat directory structure — no subdirectory packages for platform code. The `sensors/` package and `config_flow_utils/` package must be
unwrapped into root-level files. Inlining everything into `sensor.py` would create a ~2000-line monolith, so we preserve modularity via flat files with clear
naming.

#### Sensor modules

Unwrap the `sensors/` package into root-level files:

| Current               | Target              | Rationale                                                       |
| --------------------- | ------------------- | --------------------------------------------------------------- |
| `sensors/base.py`     | `sensor_base.py`    | Sensor-specific bases (energy restore, name sync) — 767 lines   |
| `sensors/panel.py`    | `sensor_panel.py`   | Panel-level sensor entities — 323 lines                         |
| `sensors/circuit.py`  | `sensor_circuit.py` | Circuit sensor entities — 398 lines                             |
| `sensors/evse.py`     | `sensor_evse.py`    | EVSE sensor entities — 81 lines                                 |
| `sensors/factory.py`  | Merge into `sensor.py` | Platform setup + factory is natural together (~400 lines combined) |
| `sensors/__init__.py` | Remove              | Re-export shim no longer needed with flat imports               |

#### Shared base entity

Create `entity.py` containing `SpanPanelEntity(CoordinatorEntity)` — the shared base class for all platforms (sensor, binary_sensor, switch, select, button).
Handles coordinator binding, device info construction, and common availability logic. This satisfies the `common-modules` Bronze rule (§2.3) and is done here
because it is a prerequisite for the sensor flattening (sensor base classes extend it).

#### Utility relocation

Move `has_bess()` from `sensors/factory.py` to `helpers.py` — it is a capability check used by `binary_sensor.py` and `button.py`, not a sensor factory
concern.

#### Config flow utils

After simulation removal (§1.1), only `options.py` (171 lines) and `validation.py` (125 lines) remain (~296 lines total). Inline into `config_flow.py`.

#### Other removals

| Current               | Action            |
| --------------------- | ----------------- |
| `simulation_configs/` | Remove (see §1.1) |

### 1.3 Manifest Adjustments

| Change                           | Reason                                  |
| -------------------------------- | --------------------------------------- |
| Remove `version`                 | Not used in core integrations           |
| Remove `issue_tracker`           | Not used in core integrations           |
| Add `integration_type: "device"` | Required field                          |
| Add `quality_scale: "bronze"`    | Initial submission target               |
| Add `loggers` array              | List logger names from `span-panel-api` |

---

## Phase 2: Architectural Alignment (Bronze)

These changes bring the integration into compliance with the 19 Bronze-tier rules required for all new core integrations.

### 2.1 Switch to `runtime_data`

**Rule:** `runtime-data`

Replace `hass.data[DOMAIN]` with typed `entry.runtime_data`.

```python
@dataclass
class SpanPanelRuntimeData:
    coordinator: SpanPanelCoordinator

type SpanPanelConfigEntry = ConfigEntry[SpanPanelRuntimeData]
```

Use `SpanPanelConfigEntry` consistently throughout the integration wherever a config entry is referenced.

### 2.2 Move Service Registration to `async_setup`

**Rule:** `action-setup`

Service actions must be registered in `async_setup()`, not `async_setup_entry()`. This allows HA to validate automations referencing these services even when
the config entry is not loaded. Inside the handler, validate that the referenced config entry exists and is loaded before executing.

### 2.3 Verify `entity.py` Base Entity

**Rule:** `common-modules`

`entity.py` is created as part of the directory flattening (§1.2). Verify that all platform entities (`sensor`, `binary_sensor`, `switch`, `select`, `button`)
inherit from `SpanPanelEntity` and that coordinator binding, device info construction, and availability logic are consolidated in the base class.

### 2.4 Add `PARALLEL_UPDATES` to All Platforms

**Rule:** `parallel-updates`

| Platform           | Value | Reason                       |
| ------------------ | ----- | ---------------------------- |
| `sensor.py`        | `0`   | Read-only, coordinator-based |
| `binary_sensor.py` | `0`   | Read-only, coordinator-based |
| `switch.py`        | `1`   | Sends commands to device     |
| `select.py`        | `1`   | Sends commands to device     |

### 2.5 Ensure Dependency Transparency

**Rule:** `dependency-transparency`

The `span-panel-api` library must satisfy all four requirements:

1. Source code under an OSI-approved license
2. Published on PyPI
3. Built from a public CI pipeline (GitHub Actions)
4. PyPI versions correspond to tagged releases

Additionally for Platinum (`strict-typing`): the library must include a `py.typed` marker file (PEP 561).

### 2.6 Verify Config Flow Test Coverage

**Rule:** `config-flow-test-coverage`

100% test coverage of `config_flow.py` including:

- User-initiated setup (happy path)
- Zeroconf discovery
- Error recovery (can complete setup after errors)
- Duplicate entry prevention
- Reauth flow
- All auth method paths (passphrase, proximity, token)

### 2.7 Verify Remaining Bronze Rules

| Rule                             | Action                                                                                  |
| -------------------------------- | --------------------------------------------------------------------------------------- |
| `appropriate-polling`            | Verify `update_interval` is reasonable (60s fallback is fine)                           |
| `brands`                         | Submit branding to `home-assistant/brands` repository                                   |
| `config-flow`                    | Already satisfied                                                                       |
| `docs-actions`                   | Document remaining service actions                                                      |
| `docs-high-level-description`    | Write integration overview for HA docs site                                             |
| `docs-installation-instructions` | Write setup guide                                                                       |
| `docs-removal-instructions`      | Write uninstall steps                                                                   |
| `entity-event-setup`             | Audit: subscriptions in `async_added_to_hass`, cleanup in `async_will_remove_from_hass` |
| `entity-unique-id`               | Already satisfied                                                                       |
| `has-entity-name`                | Already satisfied                                                                       |
| `test-before-configure`          | Verify connectivity tested during config flow before entry creation                     |
| `test-before-setup`              | Verify `ConfigEntryNotReady` / `ConfigEntryAuthFailed` raised from `async_setup_entry`  |
| `unique-config-entry`            | Verify duplicate prevention via unique ID                                               |

---

## Phase 3: Silver Requirements

Silver adds 10 rules on top of Bronze. These improve reliability and maintainability.

### 3.1 Exception Handling in Services

**Rule:** `action-exceptions`

Any remaining service actions must raise `ServiceValidationError` for invalid user input and `HomeAssistantError` for operational failures. Never raise
`ValueError` or generic exceptions from service handlers.

### 3.2 Config Entry Unloading

**Rule:** `config-entry-unloading`

Audit `async_unload_entry` to confirm all resources are cleaned up:

- MQTT client disconnection and cleanup
- Streaming callback unregistration
- Coordinator shutdown
- Platform unloading

### 3.3 Reauthentication Flow

**Rule:** `reauthentication-flow`

Already implemented. Verify complete coverage of all auth failure scenarios and that credentials are validated before saving.

### 3.4 Entity Unavailability

**Rule:** `entity-unavailable`

Verify coordinator-based entities correctly report unavailable when data fetch fails. The existing offline handling logic should cover this but needs an audit
post-simplification.

### 3.5 Unavailability Logging

**Rule:** `log-when-unavailable`

Log exactly once at `info` level when the panel becomes unreachable, and exactly once when it comes back online. If the coordinator's built-in `UpdateFailed`
handling is used, this is automatic.

### 3.6 Test Coverage

**Rule:** `test-coverage`

Achieve >95% test coverage across all integration modules. Measure with:

```bash
pytest tests/ --cov=custom_components.span_panel --cov-report term-missing
```

### 3.7 Documentation

**Rules:** `docs-configuration-parameters`, `docs-installation-parameters`, `integration-owner`

- Document all configuration options and installation parameters
- Designate a responsible maintainer in `codeowners`

---

## Phase 4: Gold Requirements

Gold adds 24 rules. These represent a polished, production-quality integration.

### 4.1 Diagnostics

**Rule:** `diagnostics`

Implement `diagnostics.py` with `async_get_config_entry_diagnostics()`. Redact sensitive data (MQTT credentials, tokens, passphrases) using
`async_redact_data()`.

### 4.2 Reconfiguration Flow

**Rule:** `reconfiguration-flow`

Implement `async_step_reconfigure` to allow updating host/port without removing the config entry. Use `_abort_if_unique_id_mismatch` to prevent account
switching.

### 4.3 Entity Device Class Audit

**Rule:** `entity-device-class`

Apply `_attr_device_class` on every entity where a matching device class exists. Key mappings:

| Entity               | Device Class                           |
| -------------------- | -------------------------------------- |
| Power sensors        | `SensorDeviceClass.POWER`              |
| Energy sensors       | `SensorDeviceClass.ENERGY`             |
| Door state           | `BinarySensorDeviceClass.DOOR`         |
| Connectivity sensors | `BinarySensorDeviceClass.CONNECTIVITY` |
| Battery SOC          | `SensorDeviceClass.BATTERY`            |
| EV charger current   | `SensorDeviceClass.CURRENT`            |

### 4.4 Entity Category Audit

**Rule:** `entity-category`

Mark diagnostic entities with `EntityCategory.DIAGNOSTIC`:

- Software version
- DSM state, grid state, run configuration
- Vendor cloud connectivity
- Ethernet/WiFi/cellular link status

Mark configuration entities with `EntityCategory.CONFIG`:

- Circuit priority select

### 4.5 Entity Disabled by Default

**Rule:** `entity-disabled-by-default`

Set `_attr_entity_registry_enabled_default = False` on noisy or supplementary entities:

- Per-circuit produced/consumed/net energy (high cardinality)
- Phase voltage sensors
- Unmapped circuit backing data sensors
- Tab attribute sensors

### 4.6 Icon Translations

**Rule:** `icon-translations`

Create `icons.json` defining all entity icons. Remove any `@property` based icon overrides from entity classes. Support state-based icon selection where
appropriate (e.g., door open vs closed).

### 4.7 Exception Translations

**Rule:** `exception-translations`

All `HomeAssistantError` and `ServiceValidationError` messages must use `translation_domain` and `translation_key` with corresponding entries in `strings.json`.

### 4.8 Entity Translations

**Rule:** `entity-translations`

Ensure all entity names are defined via `translation_key` in `strings.json` rather than hardcoded English strings. Entities with device classes that provide
automatic names can omit the translation key.

### 4.9 Additional Gold Rules

| Rule                       | Action                                                |
| -------------------------- | ----------------------------------------------------- |
| `devices`                  | Already satisfied — device info constructed           |
| `discovery`                | Already satisfied — zeroconf                          |
| `discovery-update-info`    | Update device network info from discovery data        |
| `dynamic-devices`          | Auto-add entities for circuits appearing after setup  |
| `stale-devices`            | Remove devices for circuits that disappear            |
| `repair-issues`            | Use `ir.async_create_issue()` for actionable problems |
| `docs-data-update`         | Document push streaming data refresh model            |
| `docs-examples`            | Provide automation examples                           |
| `docs-known-limitations`   | Document constraints (v1 vs v2 differences)           |
| `docs-supported-devices`   | List compatible SPAN panel models/firmware            |
| `docs-supported-functions` | Detail all available functionality                    |
| `docs-troubleshooting`     | Diagnostic guidance                                   |
| `docs-use-cases`           | Practical use case illustrations                      |

---

## Phase 5: Platinum Requirements

Platinum adds 3 final rules for the highest quality tier.

### 5.1 Async Dependency

**Rule:** `async-dependency`

The `span-panel-api` library must use `asyncio` natively. It currently uses async MQTT (aiomqtt) so this should already be satisfied. Verify no blocking I/O
calls exist in the library.

### 5.2 Inject Web Session

**Rule:** `inject-websession`

If the library makes HTTP requests (e.g., for v1 REST or v2 auth), it should accept an injected `aiohttp.ClientSession` from HA:

```python
from homeassistant.helpers.aiohttp_client import async_get_clientsession
session = async_get_clientsession(hass)
client = SpanPanelClient(host, session=session)
```

### 5.3 Strict Typing

**Rule:** `strict-typing`

- Add integration to HA core's `.strict-typing` file
- Full mypy compliance (already enforced locally)
- Library must include `py.typed` marker (PEP 561)
- Use typed config entry alias consistently
- Avoid `Any` and `# type: ignore`

---

## Phase 6: Submission Preparation

### 6.1 Code Style Alignment

- Match HA core's ruff configuration (line length, rule set)
- PEP 257 docstring conventions
- Alphabetically ordered constants, dict items, list contents
- `%` formatting in logging (not f-strings)
- Comments as full sentences ending with periods

### 6.2 Branding

Submit to `home-assistant/brands` repository:

- `icon.png` (256x256, transparent background)
- `icon@2x.png` (512x512)
- `logo.png` (horizontal logo)

### 6.3 Documentation

Write integration documentation for the HA docs site covering all required `docs-*` rules from Bronze through Gold.

### 6.4 Validation

Run HA's validation tools:

```bash
python3 -m script.hassfest
python3 -m script.translations develop
```

### 6.5 Pull Request

Open PR to `home-assistant/core` following the [integration PR template][pr-template].

[pr-template]: https://github.com/home-assistant/core/blob/dev/.github/PULL_REQUEST_TEMPLATE.md

---

## Files to Remove (Phase 1 Summary)

```text
custom_components/span_panel/
├── simulation_factory.py           # Simulation (§1.1)
├── simulation_generator.py         # Simulation (§1.1)
├── simulation_utils.py             # Simulation (§1.1)
├── simulation_configs/             # Simulation (§1.1)
├── entity_summary.py               # Naming support
├── migration.py                    # Naming migration
├── migration_utils.py              # Naming migration
├── config_flow_utils/              # Flatten into config_flow.py (§1.2)
│   ├── __init__.py
│   ├── simulation.py               # Simulation (§1.1)
│   ├── options.py                   # Inline into config_flow.py
│   └── validation.py               # Inline into config_flow.py
├── sensors/                        # Flatten to root-level files (§1.2)
│   ├── __init__.py                  # Remove (re-export shim)
│   ├── base.py                      # → sensor_base.py
│   ├── circuit.py                   # → sensor_circuit.py
│   ├── evse.py                      # → sensor_evse.py
│   ├── factory.py                   # → merge into sensor.py
│   ├── panel.py                     # → sensor_panel.py
│   └── solar.py                     # Remove if dead
└── translations/                   # Non-English (HA handles translations)
    ├── es.json
    ├── fr.json
    ├── ja.json
    └── pt.json
```

## Estimated Scope

| Phase   | Effort | Description                                               |
| ------- | ------ | --------------------------------------------------------- |
| Phase 1 | Large  | Strip ~4000 lines, restructure directories                |
| Phase 2 | Medium | Architectural changes, runtime_data, service registration |
| Phase 3 | Small  | Audit and fix coverage gaps                               |
| Phase 4 | Medium | Diagnostics, reconfigure flow, icons.json, translations   |
| Phase 5 | Small  | Library compliance, strict typing                         |
| Phase 6 | Medium | Docs, branding, validation, PR                            |
