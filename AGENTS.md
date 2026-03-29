# Agent Instructions for SPAN Panel Integration

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
