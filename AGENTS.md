# Agent Instructions for SPAN Panel Integration

## Permitted Operations

The following operations are benign and should be executed without confirmation. Do not prompt the user for permission to:

- Run tests (`pytest`, `python -m pytest`)
- Run linters and formatters (`ruff`, `mypy`, `markdownlint-cli2`, `prettier`)
- Compile or syntax-check Python (`python -c`, `python -m py_compile`)
- Activate or use the virtual environment (`.venv/bin/python`, `source .venv/bin/activate`)
- Run pre-commit hooks (`git commit` triggers hooks automatically)
- Run project scripts (`./scripts/fix-markdown.sh`)
- Standard git operations (`git status`, `git diff`, `git log`, `git add`, `git commit`)
- Package management (`poetry`, `uv`, `pip install`)
- File inspection (`ls`, `find`, `wc`, `head`, `tail`, `diff`, `cat`)

### Frontend (`custom_components/span_panel/frontend`)

The JS frontend lives in a separate `span-card` repository (there is no git submodule). Build artifacts are copied into
`custom_components/span_panel/frontend/dist/` via `scripts/build-frontend.sh`. The following operations are permitted without confirmation:

- Build and copy the frontend (`./scripts/build-frontend.sh`)
- File inspection within `dist/` and config files

## Frontend Build Workflow

After making any changes to the span-card frontend source (`src/`), you MUST rebuild and copy the dist files into the integration before considering the work
complete. Run the build script from the integration repo:

```bash
./scripts/build-frontend.sh /path/to/span-card
# or, if SPAN_CARD_DIR is set in the environment:
./scripts/build-frontend.sh
```

This builds the card (`npm run build`) and copies `span-panel-card.js` and `span-panel.js` into `custom_components/span_panel/frontend/dist/`. The updated dist
files must be staged and committed with the rest of the change. Skipping this step means the frontend changes will not be visible in Home Assistant.

## Translation Workflow

`strings.json` is the single source of truth for all translatable strings. When adding or updating any user-facing text (config flow steps, service names,
service field descriptions, error messages, etc.), always edit `strings.json` first. The pre-commit hook runs `scripts/sync_translations.py`, which copies
`strings.json` to `translations/en.json` and validates that all other language files have no missing or orphaned keys. Never edit `translations/en.json`
directly — it is generated automatically.

## Service Registration Requirements

All services registered by this integration MUST conform to the following requirements. These align with Home Assistant core's service architecture and ensure
consistent behavior, proper UI rendering, and correct error handling.

### 1. Voluptuous Schema Validation

Every service MUST have a `vol.Schema` passed to `hass.services.async_register`. Use `vol.Required` for mandatory fields and `vol.Optional` for optional ones.
Apply range/type validators (`vol.All`, `vol.Range`, `vol.Coerce`, `vol.In`) to constrain inputs at the schema level — do not defer validation to the handler.

### 2. services.yaml Field Definitions

Every service MUST have a corresponding entry in `services.yaml`. Each field MUST include a `selector` that matches its voluptuous type so the HA UI can render
the correct input widget. Required fields MUST be marked with `required: true`. Parameterless services still need an entry (the service name with no body).

### 3. strings.json Translations

Every service MUST have translations in `strings.json` under the `"services"` key:

- `services.<name>.name` — human-readable service name
- `services.<name>.description` — what the service does
- `services.<name>.fields.<field>.name` — field label
- `services.<name>.fields.<field>.description` — field help text

### 4. Error Handling with ServiceValidationError

Service handlers MUST raise `ServiceValidationError` (not generic exceptions) for user-facing errors such as missing config entries or disabled features. Always
include `translation_domain=DOMAIN` and a `translation_key` so HA can localize the error message.

### 5. Response Declaration

Services that return data MUST declare `supports_response=SupportsResponse.ONLY` (or `SupportsResponse.OPTIONAL` if the service can also be used as a fire-and-
forget action). Services that perform actions without returning data omit `supports_response`.

### 6. Runtime Data Guards

Handlers that access `entry.runtime_data` MUST verify the entry is loaded and its runtime data is the expected type before accessing coordinator or other
runtime objects. Use `hasattr` and `isinstance` checks — never assume runtime data is present.

### 7. Domain-Level Registration

Services are registered once per HA instance in `async_setup` (not per config entry). Handlers that need entry-specific data iterate
`hass.config_entries.async_loaded_entries(DOMAIN)` to find the relevant entry.
