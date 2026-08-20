# Development Guide

## Repository Layout

The SPAN Panel integration consists of three repositories:

| Repo               | Purpose                         | Branch              |
| ------------------ | ------------------------------- | ------------------- |
| `span` (this repo) | HA custom integration (Python)  | `main`              |
| `span-panel-api`   | API client library (Python)     | `main`              |
| `span-card`        | Frontend dashboard (JavaScript) | `integration-panel` |

The card repo produces two JS bundles:

- `span-panel-card.js` -- Lovelace card (standalone, HACS-distributable)
- `span-panel.js` -- Full-page sidebar panel (served by the integration)

Both bundles are committed as build artifacts in `custom_components/span_panel/frontend/dist/`.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) for Python dependency management
- [prek](https://github.com/j178/prek) for pre-commit hooks (fast, Rust-based)
- Python 3.14.2+
- Node.js 18+ and npm (for the card repo)
- [Home Assistant Core](https://developers.home-assistant.io/docs/development_environment) for local development
- [direnv](https://direnv.net/) (recommended, for automatic env setup)

## Initial Setup

```bash
# Clone all repos
git clone <span-repo-url> ~/projects/HA/span
git clone <span-panel-api-url> ~/projects/HA/span-panel-api
git clone <span-card-repo-url> ~/projects/HA/cards/span-card

# Set up the integration
cd ~/projects/HA/span
uv sync
prek install

# Set up the API library
cd ~/projects/HA/span-panel-api
uv sync

# Set up the card
cd ~/projects/HA/cards/span-card
npm install
```

## Environment Variables

The `.env` file configures paths to sibling repos and the local HA config directory. These variables are used by build scripts.

```bash
cd ~/projects/HA/span
cp .env.example .env
```

The defaults assume the standard workspace layout:

```dotenv
# Path to span-panel-api repo (for editable pip install)
export SPAN_PANEL_API_DIR=../span-panel-api

# Path to span-card frontend repo (for build-frontend.sh)
export SPAN_CARD_DIR=../cards/span-card

# Path to HA config directory
export HA_CONFIG_DIR=./ha-config
```

VS Code loads `.env` automatically into Python terminals (requires `python.terminal.useEnvFile` enabled in workspace settings). For shell use outside VS Code,
[direnv](https://direnv.net/) is recommended -- create an `.envrc` that sources `.env`:

```bash
echo 'dotenv' > .envrc
direnv allow
```

## Pre-commit Hooks

This project uses prek for pre-commit hooks. Hooks run automatically on `git commit` and check formatting, linting, type checking, translations, and test
coverage.

The linters may modify files (e.g., to sort imports or reformat). Files that are changed or fail checks will be unstaged. Review the changes, re-stage, and
recommit.

To run hooks manually:

```bash
# All hooks on staged files
prek run

# All hooks on all files
prek run --all-files
```

You can also use VS Code's `Tasks: Run Task` from the command palette to run `Run all Pre-commit checks`.

## Frontend Build Workflow

The span-card repo is independent -- it has its own git history, branches, and releases. The integration repo consumes its build output via a copy script. There
is no git submodule.

### Build and update frontend

```bash
# 1. Make changes in the span-card repo
cd ~/projects/HA/cards/span-card
# ... edit files ...

# 2. Build and copy into the integration
cd ~/projects/HA/span
./scripts/build-frontend.sh

# 3. Commit both repos
cd ~/projects/HA/cards/span-card
git add -A && git commit -m "feat: description of card changes"

cd ~/projects/HA/span
git add custom_components/span_panel/frontend/dist/
git commit -m "feat: update frontend with card changes"
```

### How the build script works

`scripts/build-frontend.sh` does three things:

1. Runs `npm run build` in the span-card repo (rollup produces two IIFE bundles)
2. Copies `dist/span-panel.js` and `dist/span-panel-card.js` into `custom_components/span_panel/frontend/dist/`
3. Prints the files and a reminder to stage them

The script reads `SPAN_CARD_DIR` from `.env` (or the environment). You can also pass the path as an argument:

```bash
# Uses SPAN_CARD_DIR from .env
./scripts/build-frontend.sh

# Via argument (overrides env var)
./scripts/build-frontend.sh ~/projects/HA/cards/span-card
```

### Local development with HA Core

When running HA Core locally, the integration is symlinked into `config/custom_components/span_panel`. The frontend JS files are served from
`custom_components/span_panel/frontend/dist/` via the `async_register_static_paths` call in `__init__.py`.

After rebuilding the frontend, restart HA to pick up the new JS. Browsers cache aggressively -- a hard refresh (Cmd+Shift+R) of the panel page also works if you
clear the `cache_headers` flag during development.

## Running Tests

```bash
# Full suite
python -m pytest tests/ -q

# Single file
python -m pytest tests/test_current_monitor.py -q

# With coverage
python -m pytest tests/ --cov=custom_components/span_panel --cov-report=term-missing
```

## Knowing what the panel publishes that nothing reads

The panel declares more than this integration surfaces, and the gap is tracked mechanically rather than by memory.

### The gate

`tests/test_declared_but_unread.py` asserts that **every property declared in a device's `$description`** is one of three things: mapped to a snapshot field by
an adapter, consumed by a known internal route (topology, dispatch, device_info, role resolution), or listed in
`tests/fixtures/unread_declarations_baseline.json` with a one-line reason.

It decides "read" **by experiment**, not by inspecting a map. For each declared property it republishes a legal different value derived from the property's own
`datatype`/`format`, rebuilds the snapshot through the real adapter, and checks whether any field a consumer reads actually moved. That is why it sees
consumption the `_PROPERTY_FIELD_MAP` cannot express, and why it caught a case where a metadata row existed while nothing read the value.

It fails in **both** directions:

- A newly declared property that reaches nothing fails the build until somebody triages it.
- A property that becomes read fails until its baseline line is deleted.

### Working with the baseline

When you surface a property, delete its baseline line in the same change. The test will tell you if you deleted one you did not surface, or surfaced one whose
line you left.

When you decide a property should _stay_ unread, add a line with an honest reason. The reasons are load-bearing — they are what stops the file becoming a list
of things nobody remembers deciding. The current entries are all permanent: deliberate skips (`status/postal-code` copies location into recorder history; Home
Assistant owns `status/time-zone`), values held for identity reasons (`pv/info/serial-number`), redundant echoes (`connection/*-device-type` dereferences to a
declared `$type`), and properties no producer publishes (`connection/count`).

### What it does not cover

The gate reads the **vendored fixture**, so it answers "what does our capture declare that we do not read". It cannot see a property a real panel starts
publishing in the field. The runtime half below is what answers that.

Note also that a property can be _read on one device and not another_ and the gate will not see it — it asks whether anything moved, not whether everything did.
`snapshot.pv` keeps the first `energy.ebus.device.pv` child and discards the rest, so a second inverter is invisible while the property still counts as read.

### The runtime half: what the panel in front of the user declares

The schema_1 adapter asks the same question of the live tree and puts the answer in **diagnostics**, under `schema_discovery`:

```json
"schema_discovery": {
  "available": true,
  "count": 9,
  "properties": [
    { "path": "discovered.circuit/connection/count", "datatype": "integer", "unit": null, "retained": false },
    { "path": "discovered.distribution-enclosure/status/postal-code", "datatype": "string", "unit": null, "retained": true }
  ]
}
```

`available: false` means the adapter has not reported metadata yet — a real state on a reconnect, and not the same as an empty report. `retained` says whether
the panel has published a value for the property, which is the declared-but-never-valued signal; it never says what the value is.

**Paths, datatypes, units and retention only — never values.** Diagnostics leave the house into issues and forum posts, and `TO_REDACT` in `diagnostics.py` is
key-based over the config entry: it knows nothing about wire property names and could not protect a value added here. `test_schema_discovery` asserts that
against the capture's own published values rather than leaving it to review.

This is **maintainer-facing only**. Nothing creates an entity, a Repair or a notification from it — including for a device that _is_ adopted, whose properties
are reported here as declarations exactly like any other. Adoption itself is the next section.

### Why discovered rows cannot reach the curated inventories

The adapter returns both kinds of row in one map, keyed by the library's `discovered.` namespace. `schema_validation.partition` splits them **before any other
question is asked**, and everything downstream — the producible gate, the `unread` inventory, the exemption annotations, the unit vocabulary — sees the curated
half only.

That partition is load-bearing rather than tidy. Every one of those inventories reads "in an adapter's map" as "this integration could read this", which a
discovered path is not: a discovered row in `unread` would bury ten deliberate entries under whatever a firmware release added, and would make the count depend
on the panel in front of the user. `test_the_unread_inventory_is_deaf_to_discovery` proves it by mutation — a synthetic discovered row changes `unread`,
`unresolved` and the unit mismatches not at all.

The test fixtures follow the same rule: `adapter_fixtures.schema_one_metadata()` hands out the curated half, and a test that wants the other half asks for
`schema_one_discovery()` by name.

### Keeping the library's answer honest

The adapter decides "read" from four enumerations of what it addresses — the metadata map, the lugs direction tables, the charge-limit resolution, and
`_CONSUMED_WITHOUT_A_ROW` for the properties it reads into the snapshot without a unit surface. A stale entry there fails _silently_, by keeping a property out
of the report. `tests/test_schema_one_discovery.py` in the library runs the same republish-and-diff experiment this gate uses and holds every entry to it in
both directions, so the report means "nothing reads this" rather than "nobody wrote it down".

## Adopting a device this integration models nothing for

The section above is about properties on devices we already read. This one is about a device type nobody modelled at all — a vendor's generator, heat pump or
second inverter, which the eBus schema explicitly permits. Such a device used to produce nothing: no device, no entity, no sign it was there.

### The rule

**The unit of adoption is a device, never a property.**

| What arrives                                     | What happens                                                                     |
| ------------------------------------------------ | -------------------------------------------------------------------------------- |
| A device type `MODELLED_TYPES` does not name     | **Adopt.** One sub-device, its properties surfaced beneath it.                   |
| A new node or property on a device we _do_ model | **Do not adopt.** It lands in `schema_discovery`; curate it in the next release. |
| A new property on a device already adopted       | Adopt, with its siblings.                                                        |

The asymmetry is where the cost calculus actually points. An adopted entity's id is machine-derived and permanent once it registers, which is only a loss where
curation is coming. On a type nobody modelled, no better id is coming and the alternative is silence.

Extra instances of a modelled type are **not** adopted. A second BESS is a multiplicity limitation of the snapshot model, not an unmodelled device, and adopting
it would stand a machine-named card beside the curated Battery describing the same hardware. The gap stays visible as a gap.

### Inside an adopted device, the node decides the destination

Keyed on the Homie node — what the eBus vocabulary defines — rather than on property names:

- **`info/*` → device-card fields.** `model`, `serial-number`, `firmware-version`, `hardware-version`, `vendor-name`. The whole node, not just the five the card
  reads: dropping only the recognised ones would surface `info/nominal-power` as a string sensor the moment a vendor declared one.
- **`connection/*` → the device link.** Topology, which is `via_device`.
- **Everything else → entities**, `EntityCategory.DIAGNOSTIC` and disabled by default.

Why by node: the capability catalogs carry **no marker** for "this value is a device reference", so the only alternative is a hard-coded property-name list —
and such a list goes stale silently. `ebus-sdk`'s own `topology.py` covers `feeds-device-id` and `fed-by-device-id` and omits `grid-forming-entity`, which lives
on the `grid` capability. A node cannot go stale that way.

### Nothing adopted enters long-term statistics

No adopted entity carries a `state_class`. `test_no_state_class_is_set_anywhere_in_the_module` reads `adoption.py` as syntax and fails if one ever appears.

Three reasons, and they are independent:

1. It is not declared on the wire and is not derivable from one. This integration ships `feedthroughEnergyProducedWh` as `TOTAL` beside
   `mainMeterEnergyProducedWh` as `TOTAL_INCREASING` — same unit, same device class, opposite classification.
2. A wrong one writes corrupt long-term statistics, and fixing the producer afterwards does not repair them.
3. Enrolling a property nobody asked for into long-term statistics is a permanent write to every install's recorder database.

A user who wants statistics from an adopted reading wraps it in a template sensor, a Riemann-sum integration or a utility meter. That is their call, made on an
entity they chose to enable.

`device_class` is enumerated in `DEVICE_CLASS_BY_UNIT` rather than inferred. A unit outside the map gets **no** device class — `%` is deliberately absent,
because its uses here are a state of charge, a confidence and a duty cycle, and no single class is right for all of them.

### The device exists even with no entities

`async_register_adopted_devices` registers each adopted device explicitly, before the platforms are forwarded, rather than letting it fall out of entity
creation. The reason is a device that has no entities to fall out of: a vendor device publishing only an `info` node resolves entirely to the device card by the
node rule, creates no entity, and so had nothing to call `async_get_or_create` for it. It produced _nothing at all_ — no device, no entity, no notification.

Running it before the platforms also makes the identity freeze single-valued: `resolve_identifier` runs once, at registration, so every entity created
afterwards resolves against a device that already exists and cannot disagree.

### Identity freezes at first sighting

`resolve_identifier` looks up **both** candidate spellings — `{panel serial}_adopted_{wire id}` and `{panel serial}_adopted_{serial}` — before minting either,
and keeps whichever already exists. Both drift in practice:

- a serial arriving _after_ adoption would move the device off its wire id, and
- a producer that derives its wire id from a serial moves the id itself when the serial appears, which is why this repository holds PV's `info/serial-number`
  unvalued.

Either move reads to Home Assistant as a device **replacement**, taking the entities and their history. The device registry is the memory, so this needs no new
persistence.

`classify_sub_device_identifier` returns `None` for any identifier carrying the `adopted` token, tested before its suffix rules — the anchor is vendor
vocabulary, and a device id ending in `pv` would otherwise classify as the panel's solar sub-device.

### Controls

`classify` routes a declaration to a platform:

| Declaration                        | Platform        |
| ---------------------------------- | --------------- |
| `boolean`, settable                | `SWITCH`        |
| `boolean`                          | `BINARY_SENSOR` |
| `enum`, settable, with a `format`  | `SELECT`        |
| numeric, settable, with a `format` | `NUMBER`        |
| anything else                      | `SENSOR`        |

A settable property with no `format` falls back to a reading because `format` is the value domain: a select with no option list and a number with no bounds are
broken controls, not safe ones. A settable `boolean` needs none — its datatype states the domain in full.

All five creators share `_create`, so `classify` is the only place a property's platform is decided. `test_every_property_reaches_exactly_one_platform` asserts
that as a partition, which is what five separate predicates could not guarantee.

Controls are disabled and diagnostic like every other adopted entity. There is deliberately no second, weaker gate — no read-only mode for settable properties.
Enabling an entity is a deliberate act, commanding it is a second one, the panel authorises the write regardless of what we create, and this integration already
ships switches that open and close breakers.

### The write, and why it is not a generic one

`SpanMqttClient.set_adopted_property(device_id, node_id, property_id, value)` publishes the write. **The lookup is the authorisation**: it resolves the property
against the current snapshot's `adopted_devices` and publishes to the `set_topic` that property carries. No topic is accepted from the caller.

That matters because the obvious alternative — a `set_property_topic(device, node, property)` member on `SchemaAdapter` — would put every curated control one
argument away, and two of them do real work on the way out:

- `set_dominant_power_source` translates `GRID` into the `ON_GRID` the v1.0 islanding assertion accepts.
- `set_evse_charge_limit` **refuses** a value above the commissioned ceiling, because publishing past it is the one write with a physical consequence.

A generic write reachable at modelled devices routes around both. Because `set_topic` is populated only for settable properties on devices `is_modelled`
rejected, a modelled device produces no `AdoptedDevice` and cannot be addressed this way however the arguments are spelled.

It also kept the change additive. A new `SchemaAdapter` member is required of every adapter package, so an install carrying an older adapter wheel would fail at
**discovery** — the whole integration, not one feature.

No payload translation and no bounds check on the way out, deliberately: the library knows nothing about an adopted property beyond its declaration, and
inventing a bound would be inventing a fact about somebody's hardware. The entity constrains the value to the declared domain and the panel stays the authority
on whether to accept it. A `NUMBER` on an `integer` property publishes `45`, never `45.0`.

Diagnostics report `adopted_devices.controls` — how many adopted properties write back rather than only reporting.

### Telling the user what was added

Additions are announced by `additions.async_announce_new_entities` as a **persistent notification**, not a Repair. An addition is not a repair: nothing is
broken and nothing needs fixing, and filing it under Repairs puts it in a category whose whole meaning is "something went wrong". The retired
`new_entities_disabled` issue is deleted at setup by `async_clear_retired_new_entity_notices`, because it was raised `is_persistent` and would otherwise stand
forever on an upgraded install with nothing left to re-derive it.

Three things it does that the Repair did not:

- **Enabled additions are announced too.** The old notice covered only `disabled_by=INTEGRATION`, reasoning that an enabled entity is already visible in the
  entity list and its history. Nobody watches their entity count, so that reasoning made every enabled addition invisible.
- **It names every entity**, rather than a count plus three examples. "What exactly was added" means all of it.
- **The record is durable.** The old diff compared the registry before the platforms against the registry after, which answers correctly exactly once — on the
  next startup the entity is already registered beforehand and the diff is empty by construction. `additions` records what it announced in a `Store`, so the
  question is "has this been announced" rather than "was this registered in the last few seconds".

It stays silent on a first install, and silent once more on the first run after this mechanism ships: an install predating the record has entities that were
never announced but are not new either, so the first pass adopts them as known.

Adopted devices are collapsed to one line with a count — `Backup Generator (6 entities)` — for the reason the whole design is device-scoped: a vendor device
declaring a dozen properties would otherwise spend the entire notification on itself and teach the user to skip it, costing them the curated additions in the
same message.

**Translations are read from this component's own `translations/` directory**, not through `homeassistant.helpers.translation`. That helper filters to the
categories Home Assistant defines, and a persistent notification is not one of them — a custom category loads as nothing at all, which was verified rather than
assumed. The strings live under a `notifications` key in `strings.json` and all five locales, with English constants in `additions._FALLBACK` so an unreadable
file costs the translation and not the notification.

### Diagnostics

`adopted_devices` in the diagnostics payload carries the device type, model, property paths, datatypes, units, `settable` flags and the platform each would
take. **No values, no device name, no serial.** Same rule as `schema_discovery` and for the same reason: `TO_REDACT` is key-based over the config entry and
cannot protect a wire value put there.

### Adopted entities declare no field paths

`snapshot.adopted_devices` is outside the curated field-path vocabulary by construction — it carries no metadata row, so the producible gate has nothing to
check it against. `adoption.py` is therefore absent from `residual_field_paths()`'s import list, and its entity classes declare no `_residual_field_paths`.

## Linting and Type Checking

```bash
# Ruff (lint + format)
ruff check custom_components/span_panel/
ruff format custom_components/span_panel/

# Mypy
python -m mypy custom_components/span_panel/

# Markdown
./scripts/fix-markdown.sh .
```

## Translation Workflow

Source strings live in `custom_components/span_panel/strings.json`. Translated files in `translations/` are synced from `strings.json` by the pre-commit hook
(`sync_translations.py`).

To add a new translatable string:

1. Add the key to `strings.json`
2. Add translations to each `translations/<lang>.json`
3. The pre-commit hook validates that all translation files match `strings.json` keys

## Panel Sidebar Registration

The integration registers a sidebar panel in `async_setup()` (domain-level, called once). Panel visibility (`show_panel`, `admin_only`) is stored in
domain-level storage (`span_panel_settings`) -- shared across all config entries. These settings are editable from any entry's options flow.

## VS Code

See `.vscode/settings.json.example` for starter settings.
