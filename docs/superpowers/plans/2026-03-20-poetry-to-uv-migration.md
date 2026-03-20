# Poetry to uv Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan
> task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Poetry with uv as the Python package manager and task runner across the entire project.

**Architecture:** Convert pyproject.toml from Poetry-specific format to PEP 621 (`[project]`) + PEP 735 (`[dependency-groups]`) + `[tool.uv]`. All `poetry run`
invocations become `uv run`. CI uses `astral-sh/setup-uv@v5`. Local path dependency uses `[tool.uv.sources]` which CI strips to resolve from PyPI.

**Tech Stack:** uv, PEP 621/735, astral-sh/setup-uv GitHub Action

---

## File Map

| Action   | File                              | Responsibility                                                                                            |
| -------- | --------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Rewrite  | `pyproject.toml`                  | Project metadata, dependencies, tool config                                                               |
| Delete   | `poetry.lock`                     | Replaced by `uv.lock`                                                                                     |
| Generate | `uv.lock`                         | New lock file from `uv lock`                                                                              |
| Modify   | `prek.toml`                       | Replace `poetry run` entries, remove poetry hooks                                                         |
| Modify   | `.github/workflows/ci.yml`        | uv setup, uv sync, uv run                                                                                 |
| Modify   | `scripts/run-in-env.sh`           | Replace poetry env detection with uv                                                                      |
| Modify   | `scripts/run_mypy.py`             | `poetry run mypy` -> `uv run mypy`                                                                        |
| Modify   | `scripts/sync-ha-deps.py`         | `poetry show` -> `uv pip show` / parse uv.lock                                                            |
| Modify   | `scripts/sync-dependencies.py`    | Update to sync manifest.json versions into pyproject.toml `[project]` deps instead of ci.yml sed commands |
| Modify   | `docs/developer.md`               | Update prerequisites and setup instructions                                                               |
| Modify   | `.github/copilot-instructions.md` | Replace all poetry references                                                                             |

---

### Task 1: Convert pyproject.toml

**Files:**

- Modify: `pyproject.toml`

Replace the Poetry-specific sections with PEP 621 / PEP 735 / uv equivalents. All `[tool.*]` sections (mypy, ruff, pyright, bandit, etc.) are unchanged.

- [ ] **Step 1: Replace `[tool.poetry]` + `[tool.poetry.dependencies]` + `[tool.poetry.group.dev.dependencies]` + `[build-system]`**

Remove:

```toml
[tool.poetry]
name = "span"
# ...
package-mode = false

[tool.poetry.dependencies]
python = ">=3.14.2,<3.15"
homeassistant = "2026.2.2"
span-panel-api = {path = "../span-panel-api", develop = true}

[tool.poetry.group.dev.dependencies]
# all dev deps...

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
```

Replace with:

```toml
[project]
name = "span"
version = "0.0.0"
description = "Span Panel Custom Integration for Home Assistant"
authors = [{name = "SpanPanel"}]
license = {text = "MIT"}
readme = "README.md"
requires-python = ">=3.14.2,<3.15"
dependencies = [
    "homeassistant==2026.2.2",
    "span-panel-api==2.3.2",
]

[dependency-groups]
dev = [
    "homeassistant-stubs==2026.2.2",
    "types-requests",
    "types-PyYAML",
    "mypy==1.19.1",
    "pyright==1.1.405",
    "ruff==0.15.1",
    "bandit[toml]==1.8.6",
    "prek>=0.3.6",
    "voluptuous-stubs",
    "python-direnv",
    "prettier",
    "radon==6.0.1",
    "pylint==4.0.5",
    "pytest>=9.0.0",
    "pytest-homeassistant-custom-component>=0.13.315",
    "isort",
]

[tool.uv]
package = false

[tool.uv.sources]
span-panel-api = { path = "../span-panel-api", editable = true }
```

Notes:

- `package = false` is the uv equivalent of Poetry's `package-mode = false`
- No `[build-system]` needed for virtual (non-package) projects
- `bandit[toml]` includes the TOML extra directly (eliminates the separate `pip install` in CI)
- Poetry `*` becomes unconstrained (just package name)
- Poetry `^X` becomes `>=X`
- `develop = true` becomes `editable = true` in `[tool.uv.sources]`
- `span-panel-api==2.3.2` version must match `manifest.json`

- [ ] **Step 2: Verify pyproject.toml is valid**

Run: `cd /Users/bflood/projects/HA/span && uv lock` Expected: Lock file generated without errors

- [ ] **Step 3: Install dependencies with uv**

Run: `cd /Users/bflood/projects/HA/span && uv sync` Expected: All dependencies installed, `.venv` created/updated

- [ ] **Step 4: Verify tools work**

Run: `cd /Users/bflood/projects/HA/span && uv run ruff --version && uv run mypy --version && uv run pytest --version` Expected: All tools report their versions

---

### Task 2: Delete poetry.lock

**Files:**

- Delete: `poetry.lock`

- [ ] **Step 1: Remove poetry.lock from repo**

Run: `cd /Users/bflood/projects/HA/span && git rm poetry.lock` Expected: File staged for deletion

---

### Task 3: Update prek.toml

**Files:**

- Modify: `prek.toml`

Three changes: replace `poetry run` in local hooks, remove poetry-check/poetry-lock hooks.

- [ ] **Step 1: Replace `poetry run pylint` with `uv run pylint`**

Line 25: `entry = "poetry run pylint"` -> `entry = "uv run pylint"`

- [ ] **Step 2: Replace `poetry run radon` with `uv run radon`** (two hooks)

Line 122: `entry = "poetry run radon"` -> `entry = "uv run radon"` Line 131 (radon-maintainability): same replacement

- [ ] **Step 3: Replace `poetry run pytest` with `uv run pytest`**

Line 148: the pytest-cov-summary entry argument string: Replace `poetry run pytest tests/ --cov=...` with `uv run pytest tests/ --cov=...`

- [ ] **Step 4: Remove poetry-check and poetry-lock hooks**

Delete lines 106-113 (the entire poetry repo block):

```toml
# Poetry check for pyproject.toml validation
[[repos]]
repo = "https://github.com/python-poetry/poetry"
rev = "2.1.3"
hooks = [
  { id = "poetry-check" },
  { id = "poetry-lock" },
]
```

- [ ] **Step 5: Verify prek hooks run**

Run: `cd /Users/bflood/projects/HA/span && prek run --all-files` Expected: All hooks pass (poetry-check and poetry-lock no longer appear)

---

### Task 4: Update CI workflow

**Files:**

- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Replace Poetry setup with uv setup**

Replace:

```yaml
- name: Install Poetry
  uses: snok/install-poetry@v1
```

With:

```yaml
- name: Install uv
  uses: astral-sh/setup-uv@v5
```

- [ ] **Step 2: Replace dependency installation step**

Replace:

```yaml
- name: Install dependencies
  run: |
    # Replace path dependencies with PyPI versions for CI
    sed -i 's/span-panel-api = {path = "..\/span-panel-api", develop = true}/span-panel-api = "==2.3.2"/' pyproject.toml
    sed -i 's/ha-synthetic-sensors = {path = "..\/ha-synthetic-sensors", develop = true}/ha-synthetic-sensors = "^1.1.13"/' pyproject.toml
    # Regenerate lock file with the modified dependencies
    poetry lock
    poetry install --with dev
    # Install bandit with TOML support
    poetry run pip install 'bandit[toml]'
```

With:

```yaml
- name: Install dependencies
  run: |
    # Remove local path source overrides so uv resolves from PyPI
    sed -i '/^\[tool\.uv\.sources\]/,/^$/d' pyproject.toml
    uv lock
    uv sync
```

Notes:

- The `sed` removes the `[tool.uv.sources]` block through the next blank line, so uv resolves `span-panel-api==2.3.2` from PyPI
- `uv sync` installs all dependency groups (including dev) by default
- `bandit[toml]` is already in `[dependency-groups]` dev, no separate pip install needed

- [ ] **Step 3: Replace all `poetry run` with `uv run`**

```yaml
- name: Format check with ruff
  run: uv run ruff format --check custom_components/span_panel

- name: Lint with ruff
  run: uv run ruff check custom_components/span_panel

- name: Type check with mypy
  run: uv run mypy custom_components/span_panel

- name: Security check with bandit
  run: uv run bandit -c pyproject.toml -r custom_components/span_panel
```

- [ ] **Step 4: Remove `poetry check` step**

Delete:

```yaml
- name: Check poetry configuration
  run: poetry check
```

- [ ] **Step 5: Update prek SKIP env var**

Replace:

```yaml
env:
  SKIP: poetry-lock,poetry-check
```

With (remove the env block entirely or clear the SKIP list if no other hooks need skipping):

```yaml
env:
  SKIP: ""
```

Or remove the `env:` block entirely if all hooks should run.

- [ ] **Step 6: Replace test runner**

Replace:

```yaml
- name: Run tests with coverage
  run: poetry run pytest tests/ --cov=custom_components/span_panel --cov-report=xml --cov-report=term-missing
```

With:

```yaml
- name: Run tests with coverage
  run: uv run pytest tests/ --cov=custom_components/span_panel --cov-report=xml --cov-report=term-missing
```

---

### Task 5: Update scripts

**Files:**

- Modify: `scripts/run-in-env.sh`
- Modify: `scripts/run_mypy.py`
- Modify: `scripts/sync-ha-deps.py`
- Modify: `scripts/sync-dependencies.py`

- [ ] **Step 1: Update run-in-env.sh**

Replace the poetry venv detection and install logic:

```bash
VENV_PATHS=(
  ".venv"
  "venv"
  ".env"
  "env"
  "$(poetry env info --path 2>/dev/null)" # Try to get Poetry's venv path
)
```

With (remove the poetry line since uv uses `.venv` by default):

```bash
VENV_PATHS=(
  ".venv"
  "venv"
  ".env"
  "env"
)
```

Replace the poetry install fallback:

```bash
# If poetry is available, ensure dependencies
if command -v poetry &> /dev/null && [ -f "pyproject.toml" ]; then
  # Check if pylint is missing
  if ! command -v pylint &> /dev/null; then
    echo "pylint not found, installing dependencies with poetry..."
    poetry install --only dev
  fi
fi
```

With:

```bash
# If uv is available, ensure dependencies
if command -v uv &> /dev/null && [ -f "pyproject.toml" ]; then
  if ! command -v pylint &> /dev/null; then
    echo "pylint not found, installing dependencies with uv..."
    uv sync
  fi
fi
```

Update the comment on line 4: `# Handles pyenv/virtualenv/poetry activation if needed` -> `# Handles pyenv/virtualenv/uv activation if needed`

- [ ] **Step 2: Update run_mypy.py**

Replace line 12:

```python
result = subprocess.check_call(["poetry", "run", "mypy"] + sys.argv[1:])  # nosec B603
```

With:

```python
result = subprocess.check_call(["uv", "run", "mypy"] + sys.argv[1:])  # nosec B603
```

- [ ] **Step 3: Update sync-ha-deps.py**

Replace the `get_ha_dependencies()` function. Change from `poetry show homeassistant --format json` to `uv pip show homeassistant --format json`:

```python
def get_ha_dependencies():
    """Get HomeAssistant's dependency pins from uv pip show."""
    try:
        result = subprocess.run(
            ["uv", "pip", "show", "homeassistant", "--format", "json"],
            capture_output=True,
            text=True,
            check=True,
        )
        ha_info = json.loads(result.stdout)
        return {dep["name"]: dep["version"] for dep in ha_info.get("dependencies", [])}
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
        return {}
```

Also update `update_pyproject_constraints()` to work with PEP 621 `[project]` format instead of `[tool.poetry]`:

Replace:

```python
deps = (
    pyproject.setdefault("tool", {}).setdefault("poetry", {}).setdefault("dependencies", {})
)
```

With logic that reads/updates the `[project]` dependencies list (which is a list of PEP 508 strings, not a dict).

- [ ] **Step 4: Update sync-dependencies.py**

The CI workflow no longer has version-specific sed commands. Instead, the version lives in `[project]` dependencies. Update this script to sync versions from
manifest.json into pyproject.toml `[project]` dependencies instead of ci.yml sed commands.

Replace `update_ci_workflow()` with `update_pyproject_dependencies()` that reads pyproject.toml, finds the dependency string (e.g., `"span-panel-api==2.3.2"`),
and updates the version to match manifest.json.

---

### Task 6: Update documentation

**Files:**

- Modify: `docs/developer.md`
- Modify: `.github/copilot-instructions.md`

- [ ] **Step 1: Rewrite docs/developer.md**

Replace full content with:

```markdown
# Development Notes

## Developer Prerequisites

- uv
- prek
- Python 3.14.2+

This project uses [uv](https://docs.astral.sh/uv/) for dependency management. Linting and type checking are accomplished using
[prek](https://github.com/j178/prek), a fast Rust-based pre-commit framework.

## Developer Setup

1. Install [uv](https://docs.astral.sh/uv/).
2. In the project root run `uv sync` to install dependencies.
3. Run `prek install` to install pre-commit hooks.
4. Optionally use `Tasks: Run Task` from the command palette to run `Run all Pre-commit checks` or `prek run --all-files` from the terminal to manually run
   hooks on files locally in your environment as you make changes.

The linters may make changes to files when you try to commit, for example to sort imports. Files that are changed or fail tests will be unstaged. After
reviewing these changes or making corrections, you can re-stage the changes and recommit or rerun the checks. After the prek hook run succeeds, your commit can
proceed.

## VS Code

See the .vscode/settings.json.example file for starter settings
```

- [ ] **Step 2: Update .github/copilot-instructions.md**

Replace all `poetry` references:

- Line 18: `Poetry (not pip)` -> `uv (not pip)`
- Lines 78-79: `poetry install --with dev` -> `uv sync`
- Line 92: `poetry run pytest` -> `uv run pytest`
- Lines 95-96: `poetry run pytest` -> `uv run pytest`
- Lines 98-99: `poetry run mypy` -> `uv run mypy`
- Lines 101-102: `poetry run ruff check` -> `uv run ruff check`
- Lines 104-105: `poetry run ruff format` -> `uv run ruff format`
- Lines 107-108: `poetry run bandit` -> `uv run bandit`
- Lines 110-111: `poetry run radon` -> `uv run radon`
- Line 121: `poetry add` / `poetry add --group dev` -> `uv add` / `uv add --group dev`
- Line 143: `poetry.lock` managed by Poetry -> `uv.lock` managed by uv, use `uv lock` to update

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "Migrate from Poetry to uv"
```

---

### Task 7: Verify end-to-end

- [ ] **Step 1: Clean install from scratch**

```bash
cd /Users/bflood/projects/HA/span
rm -rf .venv
uv sync
```

Expected: Fresh `.venv` created with all deps

- [ ] **Step 2: Run all tools**

```bash
uv run ruff format --check custom_components/span_panel
uv run ruff check custom_components/span_panel
uv run mypy custom_components/span_panel
uv run bandit -c pyproject.toml -r custom_components/span_panel
uv run pytest tests/ -q
```

Expected: All pass

- [ ] **Step 3: Run prek hooks**

```bash
prek run --all-files
```

Expected: All hooks pass

- [ ] **Step 4: Verify no poetry references remain**

```bash
grep -r "poetry" --include="*.py" --include="*.toml" --include="*.yml" --include="*.yaml" --include="*.md" --include="*.sh" .
```

Expected: No matches (except possibly in git history references or this plan file)
