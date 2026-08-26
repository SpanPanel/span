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
of things nobody remembers deciding. The current entries are all permanent: deliberate skips (`status/postal-code` and `status/time-zone`, which Home Assistant
already owns or has no use for), values held for identity reasons (`pv/info/serial-number`), redundant echoes (`connection/*-device-type` dereferences to a
declared `$type`), and properties no producer publishes (`connection/count`).

**"Unread" here means "no curated entity", not "invisible".** Since 2.1.0b7 an unread property on a _modelled_ device also surfaces through
[extension adoption](#extension-properties) as a disabled diagnostic entity, so a baseline line records a decision not to **curate** something — to give it a
designed name, a category and a place — rather than a decision to withhold it. The two skips above were written before that distinction existed and read as
though a baseline line hid a property; it does not, and the entries were reworded rather than left to mislead. A reason that turns on the cost of a _default-on_
entity is worth re-reading in that light: `postal-code`'s original reason was that surfacing it copies the user's location into recorder history, which an
opt-in disabled entity does only if the user asks for it.

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

This block is **maintainer-facing only**: nothing creates an entity, a Repair or a notification from a `schema_discovery` row — including for a device that _is_
adopted, whose properties are reported here as declarations exactly like any other.

That is a statement about this block, not about the properties in it. The same properties on a _modelled_ device also arrive as `snapshot.extension_properties`,
which does carry values and does become entities — see [Extension properties](#extension-properties). Two artefacts describing one property, joined by its
`{node}/{property}` path, with opposite audiences and opposite rules about values. Adoption itself is the next section.

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

## The suffix mappings are closed

`get_user_friendly_suffix` and `get_panel_entity_suffix` translate legacy camelCase description keys (`instantPowerW`, `instantGridPowerW`, `doorState`) into
the suffixes their entities have carried since before 2.0.8. **Do not add entries.** A new description key needs none: it resolves to itself, which is what the
sub-device builders (`build_bess_unique_id`, `build_mid_unique_id`, `build_evse_unique_id`) have always done, since their keys were written snake_case.

The reason is that the suffix is not only in the `unique_id` — it is the segment shared with the `entity_id` (`sensor_circuit.py:213`, and
`get_panel_entity_suffix`'s own docstring says so). So an edit here moves both on every installed panel: the `unique_id` costs the long-term statistics, and the
`entity_id` breaks whatever templates and automations a user wrote against it.

`tests/test_suffix_mappings_are_closed.py` holds all three dictionaries to their exact contents and fails on an added key, a removed key or a changed value —
verified by mutation, not by inspection.

This closes the question of whether to go verbatim everywhere. The migration mechanism exists and would not cost statistics, since those key on `statistic_id`
(the entity_id), which a `unique_id`-only migration preserves. But because the suffix is shared, verbatim-across-the-board would force either verbatim
`entity_id`s — `sensor.span_panel_kitchen_instantPowerW`, breaking every user reference — or a decoupling of the two, which throws away the consistency the
helper exists to provide. Closing the mapping gets the whole benefit for none of that.

## Settability is read at setup, and deliberately not re-read

Which curated control entities exist is decided once, in each platform's `async_setup_entry`, from what the panel declares about each circuit at that moment.
`switch` gates on `is_user_controllable`; `select` gates on that and on `is_never_backup`, which is a separate commissioning flag — a never-backup circuit has a
working relay and a priority the panel pins.

SPAN documents that re-commissioning a circuit in place cycles that child device's `$state` and republishes its `$description` with a new `$settable`. So a
circuit's settability can change while the integration is running, in either direction, and the setup-time gate cannot see it:

- **A circuit that stops being controllable keeps its entity.** The entity outlives its own controllability and every write it attempts now fails.
- **A circuit that becomes controllable gets no entity.** Nothing is watching it, because no entity exists to watch, so this direction cannot be noticed from an
  entity at all.

Both are safe, and the first is now well behaved. `span-panel-api` refuses a circuit relay or priority command for a circuit the panel declares non-settable,
raising rather than publishing, and each control reports that refusal as a translated error on the control the user touched. Nothing is silently published and
nothing is silently swallowed. The remedy for both directions is a reload.

**Nothing re-evaluates settability at runtime, and the reason is not the one to expect.** Permanent entity IDs are not the obstacle: the entity registry maps
`unique_id` to `entity_id`, so a reload gives every entity back the ID it had, both platforms already leave the registry entries of controls they do not create
in place, and `coordinator.request_reload()` is already used when a circuit is renamed. The mechanism is available and precedented.

The obstacle is the signal. SPAN reports a firmware defect in which the `$settable` re-toggle on the runtime re-commissioning path is skipped until the service
restarts — which is why the library refuses when _either_ the declaration or the `relay-controllable` value says no. Driving an entry-wide reload off a flag the
publisher warns can be stale would tear down every entity in the entry, and with them the grace-period, energy-dip and current-monitor state, on a value that
may not be true yet. An entity that exists and cleanly explains why it cannot act is a better answer than one that vanishes and reappears.

**If this is ever built, build it as a Repair rather than as a reload.** `schema_repairs` is the module for something re-derived from live state on every
refresh: raise a Repair naming the circuit that changed, touch no entity, and let the user choose when to reload. Waiting is cheap, and worth doing until a
panel is actually observed changing a circuit's settability in the field — so far that behaviour is documented by SPAN rather than seen here.

## Adopting a device this integration models nothing for

The section above is about properties on devices we already read. This one is about a device type nobody modelled at all — a vendor's generator, heat pump or
second inverter, which the eBus schema explicitly permits. Such a device used to produce nothing: no device, no entity, no sign it was there.

### The rule

Both halves of vendor extensibility are adopted, and **the half decides the shape**: a device nobody modelled becomes a device, a property on a device we do
model becomes a reading on that device's existing card.

| What arrives                                     | What happens                                                                                       |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| A device type `MODELLED_TYPES` does not name     | **Adopt as a device.** One sub-device, its properties surfaced beneath it. This section.           |
| A new node or property on a device we _do_ model | **Adopt as a reading** on that device's card — never a new device. [Below](#extension-properties). |
| A new property on a device already adopted       | Adopt, with its siblings.                                                                          |
| A second instance of a modelled type             | **Not adopted.** See below.                                                                        |

The two differ in what they can promise. An adopted _device_ is a card nothing else was ever going to describe. An adopted _property_ sits beside curated
entities on a card this integration designed, so it is deliberately the plainer thing: read-only, wire-named, and carrying no expectation that curation will one
day rename it into something better.

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

### The proxy link is recorded, and the nesting is not built

`AdoptedDevice` carries `parent` (the device id it declares as its parent) and `proxied` (whether that parent is a peer rather than the tree root). Adoption
does not act on either: every adopted device is registered under the panel with `via_device_id`, exactly as every curated sub-device is.

They are carried because a _proxied_ unmodelled device is a real shape we would otherwise flatten away without leaving evidence. The library's own reference
tree contains one — `bess-mid` declares `parent: bess`, which is the `{proxier-id}-{proxied-id}` naming of the specification's `devices/proxy.md`. A vendor
gateway proxying its own sub-devices arrives the same way, and the parent link is the only structural information about how they relate.

**Diagnostics report `proxied`, never `parent`.** A device id can embed a serial — producers derive a DER's id preferring a serial over a default slug, which is
why the library holds PV's `info/serial-number` unvalued — so reporting the parent verbatim would leak the serial the block deliberately withholds. The boolean
answers a maintainer's actual question, which is whether a proxied unmodelled device has appeared at all.

**Why the nesting waits.** [python-sdk#49](https://github.com/electrification-bus/python-sdk/issues/49#issuecomment-5359203067) settled that proxied ids differ
by design — several enclosures on a shared broker each proxying the same physical device produce different ids on purpose — and that consumers correlate by
`info/serial-number`, never by device id. It also records that `ebus-sdk` 0.21.0 shipped `DeviceSpec` and `DeviceTreeBuilder`
([python-sdk#57](https://github.com/electrification-bus/python-sdk/issues/57)), with the existing graph builder still to be reconciled against it. The tree
model is under active reshaping upstream, so the fields capture the evidence and the topology waits.

That comment also strengthens two things already here. Its deferral mechanism — a `device_id` callable returning `None` defers the device until
`resolve_deferred()` — is the producer-side form of settling identity _before_ a device exists, which is what registering adopted devices ahead of the platforms
does from this end. And "there is deliberately no existence predicate … expressing it by not calling `add()` is right" is the rule the capability gates already
follow: presence in the tree is the signal, and there is no flag to consult.

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

Adopted devices are collapsed to one line with a count — `Backup Generator (6 entities)` — because a vendor device declaring a dozen properties would otherwise
spend the entire notification on itself and teach the user to skip it, costing them the curated additions in the same message.

**Extension properties collapse only above `additions.COLLAPSE_ABOVE` (five), and the asymmetry is the point.** An adopted device's line names a device that did
not exist before, which is itself the news at any count. An extension property sits on a card the user already has, so `Span Panel (2 entities)` tells them
strictly less than the two names would — which is exactly what a live b7 install produced for a postal code and a time zone. Counted per notification rather
than per device lifetime: five readings announced last month and one today is a one-line update, not a flood. The detector is `_extension_device_name`, which
tests the **unique_id** rather than the device identifier, because these live on curated cards and the card says nothing about them.

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
check it against. `adoption.py` is therefore absent from `residual_field_paths()`'s import list, and its entity classes declare no `_residual_field_paths`. The
same holds for `extension.py` and `snapshot.extension_properties`, for the same reason.

## Extension properties

A property on a device this integration **does** model — `battery-2/cell-temperature` hung off the BESS by a battery vendor. Until 2.1.0b7 it reached the user
nowhere: it became a `DiscoveredMetadata` row and stopped at the diagnostics download. `extension.py` turns it into an entity on that device's existing card.

### Where the value comes from

The library carries it, in a type built for the purpose:

| Type                                         | Carries                       | Audience                    |
| -------------------------------------------- | ----------------------------- | --------------------------- |
| `DiscoveredMetadata` (`discovered.*` paths)  | Declaration only, never value | Maintainer, via diagnostics |
| `ExtensionProperty` (`extension_properties`) | Declaration **and** value     | User, via entities          |

The same wire property appears in both, joined by its `{node}/{property}` path. **`ExtensionProperty` is deliberately not a `FieldMetadata`**: `partition()`
walks `build_field_metadata()`, so a type that cannot enter that map has no path into a payload that leaves the machine. That is the diagnostics guarantee as a
shape rather than as a rule somebody has to remember, and `test_extension_property_is_not_field_metadata` asserts it.

`schema-1`'s `addressed_rows()` is shared by `build_discovery` and `build_extension_properties` so the two cannot disagree about what "unaddressed" means. A
property counted as addressed by one and not the other would appear as an entity the diagnostics claim is ignored, or the reverse.

### The identity, which is the irreversible part

```text
span_{serial}_adopted_{scope}/{node}/{property}
```

- **Anchored on what is stable and ours** — the panel serial and the curated scope (`bess`, `mid`, `pv`, `panel`, `evse_{node}`, `circuit_{id}`).
- **Addressed by the wire path verbatim**, which is upstream's own capability-catalog spelling (`AdoptedProperty.path`, `discovery_path()`). Verbatim is what
  makes it injective: the id _is_ the address. Normalising hyphens would collapse `battery-2` + `cell-temperature` and `battery` + `2-cell-temperature` into one
  id, which `test_the_pairs_a_normalising_grammar_would_collapse_stay_distinct` pins.
- **Never through `get_user_friendly_suffix`**, which de-_dots_ rather than de-hyphens and substitutes a curated suffix on a mapping hit.
- **Not the eBus proxy composition.** `{proxier-id}-{proxied-id}` is upstream's device-handle spelling, and upstream states those handles are not identities:
  they differ across enclosures and are unstable across the proxy-to-native transition. An id anchored on one would rename itself when a device stopped being
  proxied, and nothing here migrates, so there would be no recovery.
- An address outside the Homie charset (`[a-z0-9-]`) is **refused, not sanitised** — sanitising is what would make the slash-split ambiguous. It stays visible
  in diagnostics.

The slash distinguishes the two adoption grammars: device-level ids contain none.

### Terminal identity

An adopted extension is never promoted, re-sourced, re-homed or migrated. It changes only on an **external** trigger — the publisher stops publishing it, or
better metadata arrives. Three consequences worth knowing before changing any of this:

- **Curation is never blocked by one existing.** If a property is later curated, the curated entity is a _new_ entity with its own id and history; the adopted
  one is not renamed into it. Ids are permanent, identity is not, and an earlier draft of this design built a registry take-over path to avoid that — it was
  cut, because it rested on the library ceasing to emit the row in lockstep with curation, a two-repo promise whose conformance test cannot distinguish
  "curation mapped it" from "the capture was regenerated without it".
- **Nothing is ever removed by this integration**, and there is no engagement test anywhere. A row the user deletes is recreated — disabled — at the next setup
  while the property is still published, so deletion is not suppression. It sticks exactly when publishing has stopped, because then nothing exists to recreate
  it from. That is why no suppression feature exists: the delete button already means "hide until next reload" for a live reading and "clear it out" for a dead
  one, decided by the wire.
- **A property that stops being published reads unknown rather than disappearing.** Silence does not distinguish gone from not-yet-arrived.

### What metadata may reshape, and what it may not

| Attribute                                   | Revisable later?                              |
| ------------------------------------------- | --------------------------------------------- |
| `entity_category`, device class, unit, name | **Yes**, freely — no id change, no statistics |
| Platform (`sensor` vs `binary_sensor`)      | **No.** The domain is baked into `entity_id`  |
| `state_class`                               | Never set at all                              |

The free half is free _because_ of the never half: these entities carry no `state_class`, so they write no long-term statistics, so a later unit or device-class
change has nothing to reinterpret. Contrast a curated entity, where changing a unit under a `state_class` is the unrepairable case.

The platform is enforced in `resolve_platform`, not remembered: whatever domain the id is already registered under wins, however the declaration later changes.
`async_update_entity` raises `ValueError("New entity ID should be same domain")`, so re-deriving the platform from better metadata would not move a row — it
would strand it and mint a second entity beside it. `test_the_platform_a_row_is_born_under_is_the_one_it_keeps` is the guard.

### Read-only, and why disabled-by-default does not gate it

No switches, selects or number boxes, even where the panel declares the property settable — `classify_extension` is `adoption.classify` with its three control
rows deleted. The worked bypass: a vendor publishes `acme/charge-limit` beside the curated EVSE limit. The curated number goes through
`evse_charge_limit_payload()`, which **refuses** a value above the commissioned ceiling. An auto-generated number on the same device, fed by a generic set
topic, publishes whatever the user types on the same wire. The islanding assertion is the same shape — schema_1 translates `GRID` into `ON_GRID`.

"Disabled-by-default gates the control", which `classify` argues for unmodelled devices, does not transfer: there the hazard is user intent, here it is semantic
interaction with curated logic the user cannot see. The library enforces it structurally — `ExtensionProperty` has no set-topic member to populate, and
`set_adopted_property` still resolves only against `adopted_devices`.

### The cap

`MAX_PER_DEVICE` (60) bounds what one **wire device** may mint — counted on `subject_key`, not on the card. The panel, every circuit and both lugs render on the
panel's card, so counting per card would pool thirty-five devices against one allowance and truncate a 32-circuit panel at two vendor properties each, with no
misbehaving publisher anywhere. Overflow raises a durable notice (`async_notice_declined_extensions`, once at setup rather than once per platform), because a
truncation the user cannot see is the one thing worse than the truncation: sixty of a device's eighty readings looks exactly like a device with sixty.

**An id the registry already holds is never displaced.** The cap admits in adapter emission order, which tracks the wire, so a firmware update declaring a new
property earlier shifts everything after it. Capping on arrival order alone would let a new property evict a standing entity — whose registry row is permanent,
and for which nothing would ever build an entity again, leaving it unavailable forever with a stranger in its slot and no migration path by design. `adoptable`
therefore partitions registered from new, admits every registered row, and applies the cap only to the rest. Registry rows are permanent here and nothing
removes them, so a vendor node declaring hundreds of properties would otherwise put hundreds of rows in every entity picker on every install that met it, with
no later release able to take them back. Deliberately far above any real device — the sixteen `pcs` properties are the largest curated example — so it is a
backstop against a misbehaving publisher rather than a policy on normal ones.

### The prominence hint

`prominence_hint()` is advisory only: everything arrives `DIAGNOSTIC` regardless, and the hint rides along as an entity attribute for curation triage. Ranked by
confidence, and the ranking is the argument:

1. **Identity-family naming → detail.** Highest confidence because it is purely _negative_ — a property named for a vendor, model, serial, part number or
   firmware build is device description.
2. **A unit in `DEVICE_CLASS_BY_UNIT` → reading.** Moderate, and it may promote but never demote, because it fails systematically in one direction: the most
   headline-worthy number a battery publishes is a `%` state of charge, and `%` is absent from that map on purpose, being equally a confidence or a duty cycle.
3. **Everything else → detail**, with `node_has_curated_siblings` recorded as corroboration rather than as a decision. Homie nodes are organisational, not
   editorial.

The real fix is upstream: a declared `role` on the property, proposed in `SpanPanel_Docs/span/docs/dev/ebus-property-role-proposal.md`. Until then the ranking
is the shipping plan, and `entity_category` being free to revise is what makes a conservative default cheap.

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
