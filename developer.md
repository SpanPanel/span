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
publishing in the field. That is the purpose of the runtime discovery work on `feat/discovery-and-catalog-validation`: the same question asked of a live panel
and surfaced in diagnostics, so a maintainer triaging an issue can see what that fleet declares that this integration ignores.

Note also that a property can be _read on one device and not another_ and the gate will not see it — it asks whether anything moved, not whether everything did.
`snapshot.pv` keeps the first `energy.ebus.device.pv` child and discards the rest, so a second inverter is invisible while the property still counts as read.

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
