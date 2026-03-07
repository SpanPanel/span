# Home Assistant Core Submission Plan

## Overview

This document outlines the work required to submit the SPAN Panel integration to Home Assistant core. The analysis compares the current custom integration
against HA's [Integration Quality Scale][quality-scale] and [developer documentation][dev-docs].

[quality-scale]: https://developers.home-assistant.io/docs/core/integration-quality-scale/
[dev-docs]: https://developers.home-assistant.io/docs/development_index/

## Current Alignment

The integration already satisfies these core requirements:

- Config flow with zeroconf discovery, reauth, and reconfigure
- Coordinator pattern in `coordinator.py`
- Consistent entity unique IDs
- `has_entity_name = True` on all entities (via `SpanPanelEntity` base)
- Push streaming (`local_push` IoT class)
- Device info construction (consolidated in `entity.py`)
- Translations in `strings.json`
- Strict typing (mypy strict, pyright)
- Config version migration (v1 through v5)
- Good test coverage (41 test files, 267 tests)
- Typed `runtime_data` (`SpanPanelRuntimeData` / `SpanPanelConfigEntry`)
- `PARALLEL_UPDATES` on all platforms
- `entity.py` shared base class (`common-modules`)
- No service actions registered (`action-setup`)
- Flat directory structure (no subdirectory packages)
- Appropriate polling (`appropriate-polling`): 60s fallback, push is primary
- Entity event setup (`entity-event-setup`): coordinator manages streaming lifecycle
- Test before configure (`test-before-configure`): host validated before entry creation
- Test before setup (`test-before-setup`): `ConfigEntryNotReady` / `ConfigEntryAuthFailed` raised
- Unique config entry (`unique-config-entry`): serial-based unique ID with abort on duplicate
- Reconfiguration flow (`reconfiguration-flow`): host update with serial number mismatch guard
- Config entry unloading (`config-entry-unloading`): MQTT disconnect, streaming cleanup, coordinator shutdown
- Reauthentication flow (`reauthentication-flow`): v2 passphrase and proximity reauth tested
- Device classes applied (`entity-device-class`): power, energy, battery, current, connectivity, tamper, plug, charging

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

### 1.2 Inline Config Flow Utils

After simulation removal (§1.1), only `options.py` (171 lines) and `validation.py` (125 lines) remain (~296 lines total). Inline into `config_flow.py`.

### 1.3 Manifest Adjustments

| Change                           | Reason                                  |
| -------------------------------- | --------------------------------------- |
| Remove `version`                 | Not used in core integrations           |
| Remove `issue_tracker`           | Not used in core integrations           |
| Add `quality_scale: "bronze"`    | Initial submission target               |
| Add `loggers` array              | List logger names from `span-panel-api` |

---

## Phase 2: Remaining Bronze / External Items

### 2.1 Ensure Dependency Transparency

**Rule:** `dependency-transparency`

The `span-panel-api` library must satisfy all four requirements:

1. Source code under an OSI-approved license
2. Published on PyPI
3. Built from a public CI pipeline (GitHub Actions)
4. PyPI versions correspond to tagged releases

Additionally for Platinum (`strict-typing`): the library must include a `py.typed` marker file (PEP 561).

### 2.2 External Submissions

| Rule                             | Action                                                |
| -------------------------------- | ----------------------------------------------------- |
| `brands`                         | Submit branding to `home-assistant/brands` repository |
| `docs-actions`                   | Document remaining service actions                    |
| `docs-high-level-description`    | Write integration overview for HA docs site           |
| `docs-installation-instructions` | Write setup guide                                     |
| `docs-removal-instructions`      | Write uninstall steps                                 |

---

## Phase 3: Silver Requirements

Silver adds 10 rules on top of Bronze. These improve reliability and maintainability.

### 3.1 Entity Unavailability

**Rule:** `entity-unavailable`

Verify coordinator-based entities correctly report unavailable when data fetch fails. The existing offline handling logic should cover this but needs an audit
post-simplification.

### 3.2 Unavailability Logging

**Rule:** `log-when-unavailable`

Log exactly once at `info` level when the panel becomes unreachable, and exactly once when it comes back online. If the coordinator's built-in `UpdateFailed`
handling is used, this is automatic.

### 3.3 Test Coverage

**Rule:** `test-coverage`

Achieve >95% test coverage across all integration modules. Measure with:

```bash
pytest tests/ --cov=custom_components.span_panel --cov-report term-missing
```

### 3.4 Documentation

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

### 4.2 Entity Category Audit

**Rule:** `entity-category`

Mark diagnostic entities with `EntityCategory.DIAGNOSTIC`:

- Software version
- DSM state, grid state, run configuration
- Vendor cloud connectivity
- Ethernet/WiFi/cellular link status

Mark configuration entities with `EntityCategory.CONFIG`:

- Circuit priority select

### 4.3 Entity Disabled by Default

**Rule:** `entity-disabled-by-default`

Set `_attr_entity_registry_enabled_default = False` on noisy or supplementary entities:

- Per-circuit produced/consumed/net energy (high cardinality)
- Phase voltage sensors
- Unmapped circuit backing data sensors
- Tab attribute sensors

### 4.4 Icon Translations

**Rule:** `icon-translations`

Create `icons.json` defining all entity icons. Remove any `@property` based icon overrides from entity classes. Support state-based icon selection where
appropriate (e.g., door open vs closed).

### 4.5 Exception Translations

**Rule:** `exception-translations`

All `HomeAssistantError` and `ServiceValidationError` messages must use `translation_domain` and `translation_key` with corresponding entries in `strings.json`.

### 4.6 Entity Translations

**Rule:** `entity-translations`

Ensure all entity names are defined via `translation_key` in `strings.json` rather than hardcoded English strings. Entities with device classes that provide
automatic names can omit the translation key.

### 4.7 Additional Gold Rules

| Rule                       | Action                                                |
| -------------------------- | ----------------------------------------------------- |
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
├── config_flow_utils/              # Inline into config_flow.py (§1.2)
│   ├── __init__.py
│   ├── simulation.py               # Simulation (§1.1)
│   ├── options.py                   # Inline into config_flow.py
│   └── validation.py               # Inline into config_flow.py
└── translations/                   # Non-English (HA handles translations)
    ├── es.json
    ├── fr.json
    ├── ja.json
    └── pt.json
```

## Estimated Scope

| Phase   | Effort | Description                                            |
| ------- | ------ | ------------------------------------------------------ |
| Phase 1 | Large  | Strip ~4000 lines of simulation code                   |
| Phase 2 | Small  | Dependency transparency, external submissions          |
| Phase 3 | Small  | Audit unavailability, logging, coverage gaps            |
| Phase 4 | Medium | Diagnostics, icons.json, entity categories/translations |
| Phase 5 | Small  | Library compliance, strict typing                      |
| Phase 6 | Medium | Docs, branding, validation, PR                         |
