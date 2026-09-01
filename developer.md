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

## Working against unreleased library code

`pyproject.toml` installs `span-panel-api` and both adapters from `../../span/span-panel-api` as editable path dependencies. That is a committed, shared file:
whatever it names is what every import in this suite resolves to, for everyone, on every machine.

Sooner or later you need to test against library work that is not on `main` yet — an unmerged branch in a worktree beside the checkout. **Do not edit
`pyproject.toml` to point at it.** Put the worktree in your virtual environment instead:

```bash
uv sync                                                   # canonical, from the committed file
uv pip install -e ../../span/span-panel-api-<worktree>    # local override, this venv only
uv sync                                                   # back to canonical
```

`uv sync` is the undo. It rebuilds the environment from `pyproject.toml` and `uv.lock`, which never mentioned the worktree, so the override disappears with no
record of having been there. Nothing is committed at any point.

### What the second install actually does

The venv holds one `.pth` file per editable distribution — `_editable_impl_span_panel_api.pth`, `_editable_impl_span_panel_api_schema_0.pth`,
`_editable_impl_span_panel_api_schema_1.pth` — each holding the `src` directory of the main checkout. `uv pip install -e <worktree>` **replaces** the one for
the distribution it installs: same filename, rewritten to the worktree, with the `dist-info` (including `direct_url.json`, which is where the redirection is
recorded as data) replaced alongside it. There is no stacking and no shadowing, so no ordering question — the old target is gone.

It replaces only that distribution. `span-panel-api` and the two adapters are three separate distributions, and installing the bootstrap from a worktree leaves
`span_panel_api_schema_0` and `span_panel_api_schema_1` resolving to the main checkout. If the branch under test changes an adapter, or moves
`ADAPTER_CONTRACT_VERSION`, install all three:

```bash
uv pip install -e ../../span/span-panel-api-<worktree> \
                -e ../../span/span-panel-api-<worktree>/packages/schema-0 \
                -e ../../span/span-panel-api-<worktree>/packages/schema-1
```

### What not to do: `PYTHONPATH` and `MYPYPATH`

Prefixing a worktree onto `PYTHONPATH` or `MYPYPATH` looks like the same thing and is not. A worktree on the path has **no distribution metadata of its own**,
so `importlib.metadata` goes on describing the installed distribution: the right name, the right version, the right location — all of it about a copy of the
library that is not the one running. `span_panel_api.__file__` is in the worktree and every version-based check in this suite reads the installed distribution
and passes.

That is the worst available state, because it is the one where everything is green. The editable install moves the code and the metadata together, so they
cannot disagree; the path prefix moves one and not the other.

### The two guards

| Guard                                       | When        | Catches                                                                      |
| ------------------------------------------- | ----------- | ---------------------------------------------------------------------------- |
| `scripts/check-library-path.py` (prek hook) | commit time | a `span-panel-api` path in `pyproject.toml` naming anything but the checkout |
| `tests/test_library_resolution.py`          | every run   | the library that actually resolved not being the one `manifest.json` pins    |

The hook is why editing `pyproject.toml` is not the route: it rejects the commit and names this section. The test reads `span_panel_api.__file__` rather than
metadata, so it sees the `PYTHONPATH` case as well as a stale checkout. A deliberate editable override **skips** it locally, naming the path and version it
found, and **fails** under `CI` — where `[tool.uv.sources]` is deleted before installing and no legitimate override exists. A version behind or ahead of the pin
fails everywhere; that one is never intentional.

### Why the version alone is not enough

`3cbf02a` pointed both `[tool.uv.sources]` and `[tool.pyright].extraPaths` at a scratch worktree and committed it. It survived several commits and hours of
other work. While it stood, the conformance tests ran against a producer defect the library had already fixed — and every version-based check passed, because
the stale worktree declared the same version number as the corrected code.

**A version string does not identify content. Only a filesystem location does.** That is what both guards check and why neither of them checks a version on its
own.

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

## The adapter reference payloads

The conformance tests replay two real schema-adapter inputs: a full parent/child device tree and a flat `GET /api/v2/homie/schema` response. Neither is
committed here. Both are package data of the adapter distributions — `span_panel_api_schema_1/reference/parent_child_tree.json` and
`span_panel_api_schema_0/reference/homie_schema.json` — and `tests/adapter_fixtures.py` reads them through `importlib.resources`, exactly as the library's own
suite does.

So the payload the suite replays is whichever one the pinned wheel ships, and the pin in `custom_components/span_panel/manifest.json` is the record of which
that is. **Moving to a newer capture is a pin bump and nothing else**: bump the adapter version there, let `scripts/sync-dependencies.py` carry it into
`pyproject.toml` and `requirements_test.txt`, then `uv sync`. There is no copy to refresh and nothing to keep honest — a claim about which release the bytes
came from cannot disagree with the bytes when the bytes are the release's.

`tests/test_library_resolution.py` is what makes the pin mean something: it checks that the `span_panel_api` actually imported is the one the pins name, rather
than a worktree an override is pointing at.

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

The gate reads the **adapter's reference capture**, so it answers "what does that capture declare that we do not read". It cannot see a property a real panel
starts publishing in the field. The runtime half below is what answers that.

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

## Circuit entity ids: a base, not a preset

Since Home Assistant 2026.8 the user owns entity id composition — `entity_id_parts` says whether the area, the device and the entity name are in an id, and Core
assembles it. This integration no longer presets `entity_id` for circuit entities. It supplies one _base_ per entity and Core does the rest, which is how every
non-circuit entity here has always worked.

`naming.py` is the only place the wording of a circuit id is written down. `circuit_object_id_base(identifier, suffix, existing_entity_id)` joins the
naming-flag half — `Circuit 15`, `Kitchen Outlets`, `Unmapped Tab 32` — to the suffix wording, and it is the base, not the display name: the two are decoupled
on purpose so a label can be reworded without a migration. The suffix wording is read back from the id an entity already has, because two spellings have shipped
(`consumed_energy` before ids were preset, `energy_consumed` after); a new entity gets the noun-last form, matching the panel-level ids.
`ENTITY_ID_SUFFIX_FORMS` records the forms that have shipped and gains an entry only when another is _discovered_, never to introduce one.

`SpanPanelEntity.suggested_object_id` hands Core that base. It reads `_span_object_id_base`, which circuit entities set and every other entity leaves `None`, in
which case the property falls through to Core's own answer — composition from the display name, exactly as before. Core consults `suggested_object_id` only for
an entity that has not preset `entity_id`. **The property and a preset `entity_id` are not two routes to the same place**: `_async_derive_object_ids`
(`entity_platform.py`) files what the property returns as the registry's `object_id_base`, which Core then prefixes with the device and the area per the user's
parts, and files a preset `entity_id` as `suggested_object_id`, which outranks the base and is taken verbatim with no prefix at all. Setting `entity_id` to the
base would therefore ship the base as the whole id. The three circuit platforms reach the base by the same route: the sensors through `_object_id_parts`, and
the switch and the select directly.

### What R1 bounds, and what it does not

R1 — "Recreate entity IDs" must never propose a change the user did not cause — bounds the _base_ and nothing else. Read exactly: the base must reproduce an
existing entity's id **where Home Assistant composes it under the entity-id options that match how that install was built**. That is why the suffix wording is
read back and why the name half is anchored on the circuit's own name: two spellings of ours, or a relabel, must never produce an offer.

It is **not** a licence to bypass `entity_id_parts` with a hard-coded id. Where composition yields a different device half — a SPAN Drive feed sensor, whose
device is the charger and not the panel; a second panel the config flow named "Span Panel 2", whose circuit ids all say `span_panel_` because the old builder
was never told the panel's name; an install with `USE_DEVICE_PREFIX` off, whose ids carry no device at all where `has_entity_name` prefixes one — that is the
user's own configuration at work, and the offer is legitimate. Recreate offers it; nothing moves until the user presses the button (R5). The "legacy preset"
exceptions written for those three shapes were withdrawn by the maintainer on 2026-08-26, and there is no `self.entity_id = …` left in any of the four
platforms.

One consequence worth naming: a circuit sensor on a sub-device card supplies **no base at all**. Its label there is the bare description name (`Power`), so it
composes as `sensor.<charger>_power` like the charger's own sensors, and a rename on the panel cannot reach its id. Every other circuit entity sets its base
whenever `_object_id_parts` answers.

**The suffix read-back therefore does not apply to sub-device (SPAN Drive feed) sensors.** The read-back lives in `circuit_object_id_base`, and a sub-device
sensor never calls it — with no base, Core composes the entity half from the description label, which is the current one. So an existing feed energy sensor
spelled `…_energy_consumed` is offered `sensor.<charger>_consumed_energy`: the device half changes (panel → charger) and so does the suffix wording
(`energy_consumed` → `consumed_energy`, the label being `Consumed Energy`). **That is by design, not a bug.** These sensors belong to the charger's card, and
the alternative — reading a base back for them — is the bypass R1 was clarified to forbid. As everywhere else, nothing moves until the user presses Recreate and
accepts (R5).

### Releasing the registry name

Circuit-numbers mode used to deliver the panel's circuit name by writing the entity registry's `name` — the _user's_ field, which Core reads ahead of everything
else when generating an entity id, so occupying it made Recreate propose a friendly-name id for a circuit-numbered entity.
`naming.release_registry_name_written_by_older_release` hands that field back at the first start after upgrading. It clears only a value this integration would
have written — `"{circuit_name} {description name}"`, for the description's current label and every label it has carried, which is what
`SpanPanelCircuitsSensorEntityDescription.legacy_names` exists to record. Anything else in that field is the user's and is left alone.

The display name now travels as `original_name` via `_attr_name` for both modes, which means a rename pushed from the SPAN app is picked up by rebuilding the
entity: `_sync_circuit_name` asks the coordinator for a reload rather than writing the registry in place. It skips both the reload and the name where the
registry holds a `name`, since the user's field outranks the panel's and reloading could not change what is displayed.

## The suffix mappings are closed

`get_user_friendly_suffix` and `get_panel_entity_suffix` translate legacy camelCase description keys (`instantPowerW`, `instantGridPowerW`, `doorState`) into
the suffixes their entities have carried since before 2.0.8. **Do not add entries.** A new description key needs none: it resolves to itself, which is what the
sub-device builders (`build_bess_unique_id`, `build_mid_unique_id`, `build_evse_unique_id`) have always done, since their keys were written snake_case.

The reason is that the suffix is not only in the `unique_id` — it is the segment shared with the `entity_id` (it is the `suffix` half of
`naming.circuit_object_id_base`, and `get_panel_entity_suffix`'s own docstring says so). So an edit here moves both on every installed panel: the `unique_id`
costs the long-term statistics, and the `entity_id` breaks whatever templates and automations a user wrote against it.

`tests/test_suffix_mappings_are_closed.py` holds all three dictionaries to their exact contents and fails on an added key, a removed key or a changed value —
verified by mutation, not by inspection.

This closes the question of whether to go verbatim everywhere. The migration mechanism exists and would not cost statistics, since those key on `statistic_id`
(the entity_id), which a `unique_id`-only migration preserves. But because the suffix is shared, verbatim-across-the-board would force either verbatim
`entity_id`s — `sensor.span_panel_kitchen_instantPowerW`, breaking every user reference — or a decoupling of the two, which throws away the consistency the
helper exists to provide. Closing the mapping gets the whole benefit for none of that.

## Settability is re-read, and the reader is the coordinator

Which curated control entities exist is decided in each platform's `async_setup_entry`, from what the panel declares about each circuit. `switch` gates on
`circuit_has_a_breaker_switch`; `select` gates on `circuit_has_a_priority_select`, which additionally excludes `is_never_backup` — a separate commissioning
flag, since a never-backup circuit has a working relay and a priority the panel pins.

Both predicates live in `helpers.py` rather than in their platforms, because two callers read them and a second copy would drift invisibly: a switch left on the
dashboard offering a control the panel refuses, or a circuit the panel has commissioned that never grows the switch it earned. `helpers.py` is also the only
place that sits below all three: `switch.py` and `select.py` import the coordinator, so the predicate they share with it cannot live in either of them.

SPAN documents that re-commissioning a circuit in place cycles that child device's `$state` and republishes its `$description` with a new `$settable`, so a
circuit's settability can change while the integration is running, in either direction. `SpanPanelCoordinator._check_settability_change` watches both from one
place, on every push:

- **A circuit that stops being controllable** can be seen from its own entity — which is where this used to be watched, and it is only one of the two edges.
- **A circuit that becomes controllable has no entity to see it with**, so only a reader over every circuit can catch that edge at all. That is the coordinator,
  and putting both edges there keeps one answer rather than two half-answers.

`_read_settability` builds `{circuit_id: (switch?, select?)}` from the same two predicates the platforms gate on, and a difference against the previous reading
calls `request_reload()`. A reload rather than a quiet availability change, because under the new answer the entity should not exist, or should exist and does
not, and creating and removing entities is what `async_setup_entry` is for. Permanent entity IDs are not an obstacle: the registry maps `unique_id` to
`entity_id`, so a reload gives every entity back the ID it had, and both platforms already leave the registry entries of controls they do not create in place.

Two details worth keeping:

- **Only circuits present in _both_ readings are judged.** A circuit can drop out of a snapshot and come back — the platforms guard for exactly that — and
  counting an absence as a settability change would turn a flap into a reload loop. Membership is still carried forward, so a circuit that leaves and returns
  with a different answer is caught on its return.
- **The baseline is updated on the same pass that asks for the reload**, so a settled panel costs one dict comprehension per push and a changed one asks exactly
  once.

The library is the backstop under all of this: `span-panel-api` refuses a circuit relay or priority command for a circuit the panel declares non-settable,
raising rather than publishing, and each control reports that refusal as a translated error on the control the user touched. SPAN also reports a firmware defect
in which the `$settable` re-toggle on the runtime re-commissioning path is skipped until the service restarts, which is why the library refuses when _either_
the declaration or the `relay-controllable` value says no. A stale flag therefore costs a reload rather than a wrong control.

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

### Nothing adopted enters long-term statistics unless its owner asserts one

No adopted entity carries a `state_class` this integration chose. `test_no_state_class_is_set_anywhere_in_the_module` reads `adoption.py` as syntax and fails if
one ever appears there; a user-asserted one reaches the entity through the description helpers in
[`curation.py`](#the-description-helpers-are-the-only-place-state_class-is-spelled), which is the only module in the integration that spells the word.

Three reasons the integration will not pick one itself, and they are independent:

1. It is not declared on the wire and is not derivable from one. This integration ships `feedthroughEnergyProducedWh` as `TOTAL` beside
   `mainMeterEnergyProducedWh` as `TOTAL_INCREASING` — same unit, same device class, opposite classification.
2. A wrong one writes corrupt long-term statistics, and fixing the producer afterwards does not repair them.
3. Enrolling a property nobody asked for into long-term statistics is a permanent write to every install's recorder database.

None of the three is an argument against the _user_ choosing one, and all three are arguments for it being their choice rather than a default: they own the
vendor device, so they are not guessing, and the assertion is stored where it can be seen and undone. A user who would rather not assert one, or who wants a
different derivation, still wraps the reading in a template sensor, a Riemann-sum integration or a utility meter — either way it is their call, made on an
entity they chose to enable.

`device_class` is enumerated in `DEVICE_CLASS_BY_UNIT` rather than inferred, and a curated record overrides whatever that map answers. A unit outside the map
gets **no** device class — `%` is deliberately absent, because its uses here are a state of charge, a confidence and a duty cycle, and no single class is right
for all of them.

### The device exists even with no entities

`async_register_adopted_devices` registers each adopted device explicitly, before the platforms are forwarded, rather than letting it fall out of entity
creation. The reason is a device that has no entities to fall out of: a vendor device publishing only an `info` node resolves entirely to the device card by the
node rule, creates no entity, and so had nothing to call `async_get_or_create` for it. It produced _nothing at all_ — no device, no entity, no notification.

Running it before the platforms also makes the identity freeze single-valued: `resolve_identifier` runs once, at registration, so every entity created
afterwards resolves against a device that already exists and cannot disagree.

### The device-level grammar is not injective, and the collision is caught

`adopted_unique_id` spells `span_{serial}_adopted_{anchor}_{suffix}` and builds the suffix through `get_user_friendly_suffix`, which flattens the hyphens. That
is what makes it read like a curated suffix, and it is also what collapses `battery-2` + `cell-temperature` and `battery` + `2-cell-temperature` onto one id —
exactly the collision the extension grammar above avoids by carrying the path verbatim.

The encoding does not change. An adopted `unique_id` is as permanent as a curated one, so adopting the injective form would move every id an install already
holds and strand the entities keyed on them, with nothing to migrate them back. The collision is handled where the entities are built instead: `_create` claims
one id per platform, the **lexically first wire path** keeps it, and the other is skipped with a WARNING naming both. Handing both to Home Assistant registers
one and drops the other permanently, with one core log line naming an id the user cannot map back to anything on the wire.

**Lexically first, not first to arrive.** `_create` sorts each device's properties by wire path, so the survivor is a function of the declarations alone.
Picking by adapter emission order would have tracked the wire: a firmware update declaring the two colliding properties the other way round would then have
changed what a standing entity reads, silently, since the `unique_id` does not move and nothing here migrates it. Registry preference — how the extension cap
resolves its equivalent problem — is not available, because both properties resolve to the _same_ `unique_id` and the registry row cannot say which of them put
it there.

The injective encoding belongs to whatever identity namespace comes next, and adopting it there is a maintainer's ruling rather than a fix.

### A device or a property that arrives after setup

Nothing here adds entities dynamically, so a vendor device or extension property that appears an hour after setup reaches the user through the capability reload
and nothing else. `detect_capabilities` therefore carries one token per adopted device id and one per extension subject/path alongside the named flags, and
`_check_capability_change` fires on set _expansion_ exactly as it does for `bess` or `pcs`. One token each rather than one hash of the set: a hash of a
shrinking set is as "new" as a hash of a growing one, so a single token would reload the integration when a device left the tree and flap it on one that came
and went.

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

`adopted_curation` is the companion block, and it is `CurationOverlay.as_dicts()` verbatim: every stored record, keyed by its curation key, carrying its enum
values and nothing else. It withholds under the same rule for a narrower reason — the keys are wire addresses and the values are Core enum members, so there is
no wire value and no user free text in the block by construction. The free text a curated row does have (its name and its icon) lives in Core's registry rather
than in this store, so a diagnostics download cannot leak it from here at all. What the block answers is the question worth asking of a support attachment:
whether a surprising entity is surprising because a user asserted something, and which field it was.

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

| Attribute                                   | Revisable later?                                                                         |
| ------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `entity_category`, device class, unit, name | **Yes**, freely — no id change, no statistics                                            |
| Platform (`sensor` vs `binary_sensor`)      | **No.** The domain is baked into `entity_id`                                             |
| `state_class`                               | **User-curated only**, through the [curation store](#curation-metadata-the-user-asserts) |

The free half is free _because_ of the never half, and curation does not spend it: an uncurated row still carries no `state_class`, still writes no long-term
statistics, and still has nothing for a later unit or device-class change to reinterpret. What changes is who may end that: the owner of the device, explicitly,
on one row at a time — and from that point the row is in the same position as a curated entity, where changing a unit under a `state_class` is the unrepairable
case. That is why the curate command answers `incompatible_device_class` rather than storing a device class the declared unit does not admit.

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

**Told once, not once per setup.** The overflow is re-derived from the same wire at every setup, so raising it through `async_raise` put it back on screen after
every restart and reload — un-dismissable in practice, which is the opposite of what `notices.py` promises. It goes through `async_raise_on_change` instead,
which records what was announced in the notices store and raises again only when that changes. The fingerprint is the rendered device/count list rather than the
message, so another device overflowing is news and a translation update is not; dismissal does not clear the record, because clearing it there is the defect.

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
3. **Everything else → detail**, by fall-through rather than a third signal. The node a property hangs off is the obvious candidate for one and is deliberately
   not consulted: Homie nodes are organisational, not editorial.

The real fix is upstream: a declared `role` on the property, proposed in `SpanPanel_Docs/span/docs/dev/ebus-property-role-proposal.md`. Until then the ranking
is the shipping plan, and `entity_category` being free to revise is what makes a conservative default cheap.

## Curation: metadata the user asserts

Adoption's refusal was never "adopted entities may not have statistics" — it was "the integration will not guess", and those are the same rule only while nobody
who does know the answer has anywhere to say it. `curation.py` is that place. It owns three fields and no others: `state_class`, `device_class`, and promotion
out of `EntityCategory.DIAGNOSTIC`. Everything else a user might want to change about an adopted entity — its name, icon, area, display unit, precision, and
whether it is enabled at all — is registry state Core already owns, and this integration writes none of it.

Identity is untouched in every case. A curated row keeps its `unique_id`, its `entity_id` and its platform: the overlay changes what an entity declares, never
what it is. That is what makes this safe to apply to entities whose ids are [permanent by design](#terminal-identity) — it is metadata handed to an existing
identity, not a second identity namespace.

### The store is an overlay keyed by wire address

`helpers.storage.Store` at `span_panel.curation.{entry_id}`, one per config entry for the same reason `additions.py` has one: two panels in one house curate
independently. The stored shape is `{"records": {key: {field: value}}}`, and a record holds only what the user asserted — a missing field means the adopted
default, and a missing key means the row was never curated at all.

The keys are scope-prefixed wire addresses rather than `unique_id`s:

| Half                                | Curation key                     | Built by                 |
| ----------------------------------- | -------------------------------- | ------------------------ |
| Vendor reading on a modelled device | `{scope}/{node}/{property}`      | `extension_curation_key` |
| Entity on an adopted device         | `{identifier}/{node}/{property}` | `adopted_curation_key`   |

The prefix is what makes a key injective: `path` is `{node}/{property}` on both models and is unique only within one device. The two namespaces cannot collide,
because only an adopted identifier carries the `_adopted_` token. Neither key goes through `get_user_friendly_suffix`, which is what makes `adopted_unique_id`
[deliberately non-injective](#the-device-level-grammar-is-not-injective-and-the-collision-is-caught) — keying the store on a `unique_id` would have inherited
that collision and let one record reach two wire addresses.

**A record asserting nothing clears its key rather than being stored.** Its stored form is `{}`, which `parse_record` refuses, so writing it would leave a
record on disk that the next load reports as unreadable — the warning meant for a damaged or hand-edited store — and the save after that would delete, over a
value the signature accepts. Save may not write what load rejects.

Records are never pruned. One whose wire path stops being published goes inert rather than being deleted, which is the same "the integration never decides a
row's fate" stance the rest of adoption takes. The whole store does go when the config entry is removed (`async_forget_curation`), and that one is deliberate:
the keys are wire addresses rather than registry ids, so a store left behind is one the next entry for the same panel would load and apply, re-asserting
metadata the user removed the panel to be rid of.

### Validation refuses at save, and runs again at construction

`validate_record` refuses rather than warns, because a stored record is applied unattended at every future setup — a warning would be read once, by nobody.
Everything decidable without the wire is decided in the websocket schema instead (enum membership, the one storable `entity_category`, the key's charset), so
only cross-field questions reach the validator: a `state_class` needs a sensor row with a numeric datatype, a `device_class` must belong to its platform's enum
and must fit both what the row declares it is and the unit it declares (Core's own `NON_NUMERIC_DEVICE_CLASSES` and `DEVICE_CLASS_UNITS`), and a control row
accepts prominence and nothing else.

The datatype half of that came later than the unit half and closes a real gap: gating on the unit alone let a text row be offered `power_factor`, `aqi` and
`monetary`, which constrain no unit and so passed vacuously, each of which reads `unknown` for the life of the install. Which side of Core's partition a row
falls on is `util.declares_a_number`'s answer — a numeric `$datatype` or a declared unit — which is the same predicate `AdoptedSensor.native_value` uses to
decide whether to parse. Sharing it is the point: a row whose reading is parsed as a float and whose only offered device class was `enum` would be incoherent,
and Core refuses to render a state carrying both a unit and a non-numeric device class at all.

`declares_a_number` is also why a unit-less numeric reading is a number. The unit used to stand in for "this is numeric", so a bare `integer` count published as
`"42"` reached the state machine as text — harmless while an uncurated row asserted nothing about itself, and not harmless once its owner could put a
`measurement` on exactly that row and hand the recorder a string under it. The union rather than the datatype alone, because a publisher that omits a
`$datatype` still declares a unit, and nothing that parsed before may stop parsing.

The same validator runs again at construction, where it drops rather than refuses. A record can go stale between the save and a later setup — the vendor may
change a row's unit or datatype — so `sanitise` re-measures each field independently, keeps the ones that still validate, and `CurationOverlay.for_row` emits
one warning naming what it dropped. It never raises: curation must not be able to block setup. It never deletes either, because the wire may revert and the
user's other assertions are still good.

That is also why the list command reports a record **as stored** rather than as it would be applied, beside a `stale_fields` list naming the difference.
Reporting the sanitised form would show the user an assertion they never made and hide that theirs was dropped.

### The description helpers are the only place `state_class` is spelled

`adoption.py` and `extension.py` each carry an AST guard asserting the token appears nowhere in them — not as a keyword, not as an `_attr_state_class` target,
not as a `SensorStateClass` name. Both stay true while their entities carry curated state classes, because neither module builds its own description: both call
`curation.sensor_description`, which takes the wire path, the declared unit, the `DEVICE_CLASS_BY_UNIT` default and the record, and returns the
`SensorEntityDescription` the entity is constructed from. `binary_sensor_device_class` and `entity_category_for` do the same job for the other two fields.

This ends up stricter than the design asked for. The plan was to relax the adoption guard into "the keyword is permitted when its value comes from the curation
interface"; routing through a helper meant it did not have to relax at all, and the guard newly added for `extension.py` could be the same absolute form rather
than a weaker one. A guard that admits one shape of exception is a guard somebody has to re-read before trusting.

### The two commands write no registry state

`websocket_adopted.py` defines `span_panel/adopted/list` and `span_panel/adopted/curate`, and `websocket.py`'s `async_register_commands` registers them beside
`panel_topology` — the import runs that way and only that way, so no cycle can appear as further commands join. Both are `@require_admin`, both take the main
panel's device registry id, and both answer `panel_topology`'s error codes from the same resolution — a consumer that learned one set does not meet a second.
[websocket-api.md](websocket-api.md) is the wire contract; what matters here is the boundary.

**Enabling is Core's act, and so are naming, icons, areas, display units and precision.** `config/entity_registry/update` already exposes all of them, already
requires admin and already carries the undo, so duplicating any of it here would mean two writers for one field and no rule about which wins. What is left over
is exactly what Core has nowhere to put — a state class, a device class and prominence for an entity built from a vendor declaration — and that is the whole of
what `curate` stores. `entity_category` is the interesting one: it _is_ a registry column, but it is absent from that websocket's schema, which is why promotion
has to come from us.

Both commands derive their rows through one function, `_rows`, using the same helpers the entity builders use — `resolve_identifier` and `classify` for an
adopted device, `adoptable` and `resolve_platform` for a vendor reading. A second derivation would let the editor disagree with the entities it edits: offering
a state class for a row that is really a control, or a key `curate` cannot resolve. `curate` re-derives rather than trusting the key it was handed, because the
store is keyed on wire addresses and a key nothing publishes would be held forever — read by no entity and shown on no list.

`_rows` inherits the adopted-device collision rule as a **skip** rather than a listing. A row whose `unique_id` was claimed by a lexically earlier wire path is
left out entirely, because `entity_id` resolves by (platform, `unique_id`): listing it would report the _winner's_ entity beside the loser's curation key,
inviting a record saved against an entity that will never read it under a live `entity_id` saying it will. `_create` already warns and names both addresses, so
the skip is silent.

### The reload is the mechanism, not a courtesy

A save has exactly three effects: the record is written, the entry is scheduled for reload, and the result is returned. The reload is the half that reads as
politeness and is not. An entity description is fixed when the entity is constructed, so a record reaches its entity only by that entity being built again — and
being built _with_ it, because a `state_class` that first appears after states have been recorded is a statistics reset rather than a metadata change.

The response also carries advisory `warnings`, which are consequences of a save rather than objections to it: the write has already happened, and the user asked
for it. `statistics_removed` fires on what a save _leaves_ rather than on how it was spelled — a record narrowed to its other fields drops a state class exactly
as clearing the whole record does — and names Core's answer to that, which is to raise its `state_class_removed` repair and stop compiling statistics for the
entity. Statistics already collected are not deleted. `total_increasing` is the other warning, and it reinterprets a reading rather than describing it:
`sensor/recorder.py`'s `reset_detected` reads a drop of more than a tenth as a meter reset, so a reading that legitimately falls manufactures consumption.

## Runtime data lives in `runtime.py`, and one helper answers for it

`SpanPanelRuntimeData` and `SpanPanelConfigEntry` used to live in `__init__.py`, which imports every platform — so naming the entry type meant importing the
whole integration. Seven modules imported them from the package root and five more reached for them from inside functions to break the cycle. They now live in
`runtime.py`, whose only runtime dependency inside this package is `control_gate` (itself a leaf); the coordinator is needed there as an annotation only. The
package root re-exports both names, so `from custom_components.span_panel import SpanPanelRuntimeData` keeps working, and every deferred import of them is a
top-level one again.

**Use `runtime.loaded_runtime_data(entry)` rather than an inline `hasattr` / `isinstance` pair.** Every service is registered domain-wide and walks
`async_loaded_entries(DOMAIN)`, so each one has to ask the same question before touching an entry, and this used to be five copies of it — plus two more in the
package root, in `async_unload_entry` and `async_remove_config_entry_device`, which is why the helper lives in `runtime` (a leaf that already owns the type)
rather than in `services`. Both halves of the check are real, which is why the helper is written the way it is: `ConfigEntry.runtime_data` is a bare annotation
that core _deletes_ on unload, so the attribute is genuinely absent on an entry that has not finished setting up (hence `getattr` with a default), and what is
there is whatever the owning integration put there, so `isinstance` is what says it is ours. AGENTS.md service point 6 names this helper.

## Why the CA pinning behaves as it does

The README says what the pinning does; this is why each of those choices was made rather than the obvious alternative. All of it was written for the README and
moved here, because it answers "why is it like that" rather than "what do I do".

**The fingerprint is not shown at first contact, on purpose.** Comparing it against another source is what closes the active-in-path case, and at first contact
there is nothing to compare against — SPAN does not publish the value, so the question could only ever be answered by pressing Submit. A dialog that asks a
question the user cannot answer trains them to dismiss it, which costs the one time it matters. So the value is put where it can actually be used instead:
diagnostics under `panel_ca`, the setup log, and any other install of this integration on the same panel.

**Diagnostics carry the fingerprint and not the certificate.** The certificate is public, so withholding it buys no secrecy — it is omitted because it is
multi-KB and the fingerprint is the part anyone reads.

**An unreachable panel does not stop setup.** The integration starts anyway and retries on the next startup. The exposure in the meantime is exactly the one the
entry already had before pinning existed, and refusing to start would not remove it — it would remove the integration, leaving the credential no safer while
guaranteeing an outage. Retrying closes the window at the first opportunity instead.

**Reauth keeps the anchor it acquires.** An entry that predates pinning, or whose stored authority no longer loads, goes through the certificate-authority step
before either sign-in method is offered, and keeps the anchor afterwards rather than falling back to a plaintext fetch on every connection. Reauth is a
registration — it carries the passphrase out and the broker password back, the same exchange setup performs — so it is the one flow where acquiring an anchor
first is worth a step in front of the user.

**Reauthenticate is the route that pins on screen; Reconfigure pins silently.** The certificate-authority step asks for the TLS port where the HTTP port has
been moved — the install most likely to be behind a proxy — and errors if the authority does not sign what the panel serves. Reconfiguring the entry to the
panel's own address reaches the same place, but the pin then happens during the reload that follows, announced only by a `WARNING` reading "Pinned the CA
advertised by SPAN panel …" with the fingerprint.

**A registered domain is checked once the panel reports the new certificate**, not when registration is requested. The panel regenerates its certificate around
the FQDN asynchronously, so the flow polls for the name to appear in the SAN rather than assuming the call took effect.

## The Supervisor discovery path is unguarded on ports

A pinned entry only follows a discovered host when that host serves a certificate the entry's anchor validates — `async_step_zeroconf` and the by-hand re-add
both go through that check, because the serial they match on comes from an unauthenticated endpoint anything on the LAN can answer.

`async_step_hassio` deliberately does not hold its **ports** to that check. A Supervisor discovery arrives over the authenticated Supervisor API from an add-on
the user installed, and add-ons legitimately reallocate their own ports across restarts, so holding the ports to the stored values would freeze the entry
against its own add-on.

**The host is not covered by that argument, and is not taken on the add-on's word.** An add-on republishing its container hostname has not moved the panel, and
that hostname is generally not a name the panel's certificate carries — writing it over a working host broke an entry seconds after it was created. So
`_async_hassio_host_update` probes the _configured_ host on the _newly discovered_ port, which is precisely the reallocated-port case, and keeps the stored host
whenever a v2 answer there carries this panel's serial. Only a configured host that has stopped answering for this panel is replaced, and then `CONF_HOST` and
`CONF_EBUS_BROKER_HOST` move together — moving one without the other left the entry naming two different machines.

That is a narrower check than the other two routes make, and the residual cost is stated rather than hidden: an add-on that already holds Supervisor privileges
can still move an entry whose configured host has genuinely gone away. If that trade is ever revisited, the guard is the same helper the other two routes call.

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
