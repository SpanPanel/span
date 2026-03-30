# Development Guide

## Repository Layout

The SPAN Panel integration consists of two repositories:

| Repo | Purpose | Branch |
|------|---------|--------|
| `span` (this repo) | HA custom integration (Python) | `main` |
| `span-card` | Frontend dashboard (JavaScript) | `integration-panel` |

The card repo produces two JS bundles:

- `span-panel-card.js` — Lovelace card (standalone, HACS-distributable)
- `span-panel.js` — Full-page sidebar panel (served by the integration)

Both bundles are committed as build artifacts in
`custom_components/span_panel/frontend/dist/`.

## Prerequisites

- Python 3.12+ with a virtual environment (`.venv/`)
- Node.js 18+ and npm (for the card repo)
- [direnv](https://direnv.net/) (recommended, for automatic env setup)

## Initial Setup

```bash
# Clone both repos
git clone <span-repo-url> ~/projects/HA/span
git clone <span-card-repo-url> ~/projects/HA/cards/span-card

# Set up the integration
cd ~/projects/HA/span
python -m venv .venv
source .venv/bin/activate
pip install -r requirements_test.txt

# Set up the card
cd ~/projects/HA/cards/span-card
npm install

# Configure direnv (one-time)
cd ~/projects/HA/span
echo 'export SPAN_CARD_DIR="$HOME/projects/HA/cards/span-card"' >> .envrc
direnv allow
```

## Frontend Build Workflow

The span-card repo is independent — it has its own git history, branches, and
releases. The integration repo consumes its build output via a simple copy
script. There is no git submodule.

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
2. Copies `dist/span-panel.js` and `dist/span-panel-card.js` into
   `custom_components/span_panel/frontend/dist/`
3. Prints the files and a reminder to stage them

The script needs to know where the span-card repo lives. Two ways:

```bash
# Via env var (recommended — set once in .envrc)
export SPAN_CARD_DIR=~/projects/HA/cards/span-card
./scripts/build-frontend.sh

# Via argument
./scripts/build-frontend.sh ~/projects/HA/cards/span-card
```

### Local development with HA Core

When running HA Core locally, the integration is symlinked into
`config/custom_components/span_panel`. The frontend JS files are served
from `custom_components/span_panel/frontend/dist/` via the
`async_register_static_paths` call in `__init__.py`.

After rebuilding the frontend, restart HA to pick up the new JS (browsers
cache aggressively). A hard refresh (Cmd+Shift+R) of the panel page also
works if you clear the `cache_headers` flag during development.

## Running Tests

```bash
# Full suite
python -m pytest tests/ -q

# Single file
python -m pytest tests/test_current_monitor.py -q

# With coverage
python -m pytest tests/ --cov=custom_components/span_panel --cov-report=term-missing
```

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

Source strings live in `custom_components/span_panel/strings.json`. Translated
files in `translations/` are synced from `strings.json` by the pre-commit hook
(`sync_translations.py`).

To add a new translatable string:

1. Add the key to `strings.json`
2. Add translations to each `translations/<lang>.json`
3. The pre-commit hook validates that all translation files match
   `strings.json` keys

## Panel Sidebar Registration

The integration registers a sidebar panel in `async_setup()` (domain-level,
called once). Panel visibility (`show_panel`, `admin_only`) is stored in
domain-level storage (`span_panel_settings`) — shared across all config
entries. These settings are editable from any entry's options flow.
