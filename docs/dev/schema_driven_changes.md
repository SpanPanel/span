# Schema-Driven Sensor Discovery

This document describes a phased approach to reducing the manual coupling between the SPAN Homie MQTT schema, the `span-panel-api` library, and the HA
integration's sensor definitions. The goal is not full auto-generation but a practical reduction in the maintenance surface when SPAN firmware adds, corrects,
or extends properties.

## Motivation

Today the system has three layers of hardcoded knowledge about SPAN panel properties:

1. **Homie schema** (`GET /api/v2/homie/schema`) -- declares every node type, property, datatype, unit, format, and settable flag. Self-describing and
   firmware-versioned.
2. **span-panel-api** -- hand-coded `HomieDeviceConsumer._build_snapshot()` (653 lines) maps MQTT properties to frozen dataclass fields. Sign conventions,
   cross-references, and derived state are embedded here.
3. **span integration** -- 47+ `SensorEntityDescription` instances in `sensor_definitions.py`, each with a `value_fn` lambda reaching into a specific snapshot
   field plus HA metadata (`device_class`, `state_class`, `native_unit_of_measurement`).

Adding a new sensor requires changes to all three layers. Correcting a unit requires changes to layers 2 and 3. The schema itself evolves with firmware
releases.

### Why Not Go Fully Schema-Driven

The Homie schema is self-describing but not self-correct. The 202609 changelog documented unit declaration errors (`kW` declared when values were actually `W`)
for `active-power` and PV `nameplate-capacity`. The integration's hardcoded knowledge of the correct units protected users from displaying values 1000x off. A
schema-driven integration would have propagated the error.

Additional blockers:

- **No schema versioning** -- the schema is tied to firmware releases (`rYYYYWW`), which conflates "the software running on the panel" with "the data contract
  the panel exposes." A firmware update may change dozens of things without touching the schema, or alter one property's unit declaration without changing the
  firmware version format. There is no independent schema version, no mechanism to request a specific version, and no backwards-compatibility guarantee. The
  Homie API (`/api/v2/`) is currently in beta, which explains the in-place schema mutations; post-beta breaking changes would be expected under a new endpoint
  (e.g. `/api/v3/`). The schema hash computed by Phase 1 drift detection is the best available proxy for a schema version -- a content-addressed identifier for
  the exact set of node types, properties, units, and datatypes.
- **Irreducible semantic layer** -- sign conventions, derived state machines (`dsm_state`, `current_run_config`), cross-references (EVSE `feed` to circuit),
  unmapped tab synthesis, and energy dip compensation are domain logic not representable in the Homie schema.
- **HA-specific metadata** -- `device_class`, `state_class`, `entity_category`, `suggested_display_precision` have no Homie equivalent.
- **User stability** -- HA users build automations and dashboards against stable entity IDs and sensor behaviors. Schema-driven changes that silently alter a
  sensor's unit or meaning would break installations.

The phased approach below progressively surfaces schema metadata for validation and diagnostic purposes first, then optionally for reducing entity definition
boilerplate on reviewed fields, without ever trusting the schema blindly for units or semantics and without ever exposing fields to users without human review.

## Phase 1: Schema Metadata Exposure (Validation and Diagnostics) — COMPLETE

**Status**: Implemented and tested across both repositories.

### Architectural Boundary

The integration knows nothing about Homie, MQTT, node types, or property IDs. All transport knowledge lives in `span-panel-api`. The integration sees only:

- **Snapshot field paths** -- `"panel.instant_grid_power_w"`, `"circuit.current_a"`, etc.
- **Field metadata** -- unit and datatype per field, exposed by the library in transport-agnostic terms.
- **Sensor definitions** -- the integration's own HA metadata for each sensor.

### span-panel-api Implementation

| Module                   | Purpose                                                                                                                              |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| `models.py`              | `FieldMetadata(unit, datatype)` frozen dataclass                                                                                     |
| `mqtt/field_metadata.py` | `_PROPERTY_FIELD_MAP` (Homie property → field path), `build_field_metadata()`, `log_schema_drift()`                                  |
| `mqtt/client.py`         | Retains schema across connections, builds/caches field metadata during `connect()`, detects schema hash changes and diffs properties |
| `protocol.py`            | `field_metadata` property added to `SpanPanelClientProtocol`                                                                         |

**Data flow at connect time:**

1. `SpanMqttClient.connect()` fetches the Homie schema
2. If the schema hash changed since last connection, `log_schema_drift()` diffs the old and new schemas at the property level (new/removed node types,
   new/removed properties, unit/datatype/format changes) -- all logged internally, never exposed to the integration
3. `build_field_metadata(schema.types)` iterates `_PROPERTY_FIELD_MAP`, looks up each Homie property in the live schema for its declared unit and datatype, and
   produces `dict[str, FieldMetadata]` keyed by snapshot field path
4. The result is cached on the client as the `field_metadata` property

**Field path convention:** `{snapshot_type}.{field_name}` where snapshot_type is one of `panel`, `circuit`, `battery`, `pv`, `evse`. The library defines this
convention in `_PROPERTY_FIELD_MAP` (~55 entries covering all properties that `_build_snapshot()` reads).

### span Integration Implementation

| Module                   | Purpose                                                                                                                                  |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `schema_expectations.py` | `SENSOR_FIELD_MAP` -- sensor definition key → snapshot field path (the ONLY manually-maintained data)                                    |
| `schema_validation.py`   | `validate_field_metadata()` -- unit cross-check, unmapped field reporting. `collect_sensor_definitions()` -- builds the sensor defs dict |
| `coordinator.py`         | `_run_schema_validation()` -- one-shot call after first successful refresh                                                               |

**Data flow at first refresh:**

1. `SpanPanelCoordinator._run_post_update_tasks()` fires `_run_schema_validation()` once
2. Reads `client.field_metadata` and converts `FieldMetadata` objects to plain dicts
3. `collect_sensor_definitions()` gathers all sensor descriptors into a dict keyed by sensor key
4. `validate_field_metadata()` walks `SENSOR_FIELD_MAP`, for each entry:
   - Looks up the field path in the library's metadata to get the schema-declared unit
   - Looks up the sensor key in the sensor definitions to get the HA `native_unit_of_measurement`
   - Compares them and logs mismatches
5. Reports fields in the library's metadata that no sensor references

**Validation checks:**

| Check            | Severity | Example                                                      |
| ---------------- | -------- | ------------------------------------------------------------ |
| Unit mismatch    | DEBUG    | Field metadata says `kW`, sensor definition has `W`          |
| Missing metadata | DEBUG    | Sensor reads a field the library has no metadata for         |
| Missing unit     | DEBUG    | Field metadata has no unit but sensor definition expects `V` |
| Unmapped field   | DEBUG    | Library metadata contains a field no sensor references       |

All output is DEBUG-level only -- invisible to users at default HA log levels. A maintainer enables it by setting
`logger: custom_components.span_panel.schema_validation: debug` in their HA configuration. No entity creation or sensor behavior changes.

### Tests

**span-panel-api** (`tests/test_field_metadata.py`, 15 tests): field metadata building for all snapshot types, unit/datatype correctness, enum and boolean
handling, empty schema, field path convention, generic lugs fallback.

**span integration** (`tests/test_schema_validation.py`, 13 tests): mapping structure validation, sensor keys match definitions, field paths match snapshot
attributes, unit cross-check (match, mismatch, missing), unmapped field detection, no-op when metadata unavailable.

### What This Achieves

- Zero risk to users -- no sensor behavior changes, log-only output.
- Clean architectural boundary -- integration never sees Homie/MQTT details.
- Early warning when schema-derived field metadata disagrees with sensor definitions (e.g. the kW/W error).
- Foundation for Phase 2 -- the field metadata and mapping are reusable.

## Versioning Model

The `span-panel-api` library is the gating factor for all schema changes reaching the integration. Even before SPAN corrects known unit declaration errors in
the Homie schema, the library applies the correct interpretation -- the snapshot contract defines the truth, not the schema. This isolation has two
consequences:

1. **Schema corrections (declaration-only)** -- when SPAN fixes a unit declaration (e.g. `kW` → `W`) without changing actual values, neither repo needs code
   changes. The library's `build_field_metadata()` automatically reflects the corrected declaration, and Phase 1 validation mismatches resolve themselves.

2. **Value changes** -- if SPAN changes actual transmitted values (e.g. starts sending kW-scale values to match a `kW` declaration), the library must apply a
   conversion in `_build_snapshot()` to maintain the snapshot contract. The integration bumps the library version; no other changes needed.

In both cases, the library version pins a specific interpretation of firmware data. The version sequence for a breaking firmware change:

1. SPAN releases firmware with changed property behavior
2. Library releases a new version with the conversion/adaptation
3. Integration bumps its library dependency
4. User updates the integration -- changelog explains what changed

Each step is a human decision point. No change reaches users without explicit maintainer review.

### Schema Version vs Firmware Version

The correct thing to version against is the schema, not the firmware. The `rYYYYWW` firmware identifier conflates panel software with data contract. Ideally
SPAN would provide a declared `schema_version` field -- a monotonically increasing version or a semver -- so the library can say "I understand schema versions
up to X" rather than "I was built against firmware rXXXXYY."

The current unit corrections and schema changes being made without a version bump are beta-phase behavior -- the Homie API is served at `/api/v2/` and is not
yet stable. Once the API exits beta, breaking changes to the schema would be expected to land under a new endpoint (e.g. `/api/v3/`), not as in-place mutations
to the v2 schema. This distinction matters: the current churn is not representative of the long-term maintenance burden, and the trigger criteria for later
phases should be evaluated against post-beta stability, not beta-phase corrections.

Until SPAN provides a declared schema version, the library's schema hash (computed during Phase 1 drift detection) serves as the implicit schema version. The
library could maintain a known-schema-hashes table, mapping each validated hash to the set of corrections it applies. When encountering an unknown hash, it logs
a warning (drift detection already does this) and falls back to existing corrections -- safe-by-default behavior.

### New Fields Require Human Review

A new property appearing in the Homie schema must not be automatically exposed to users. The kW/W precedent proves that schema declarations cannot be trusted
for correctness on first appearance. If a field were surfaced automatically, users would build automations on it, and a subsequent correction to its unit or
sign convention would break those automations.

The path for a new field:

1. Phase 1 drift detection logs the new property
2. A maintainer reviews the property's actual values against its declared unit and datatype
3. The library adds the field to `_build_snapshot()` and `_PROPERTY_FIELD_MAP`
4. The integration adds a `SensorEntityDescription` with verified HA metadata
5. Both repos release new versions

This is the same human-gated process used for existing fields. The library absorbs transport details; the integration adds HA semantics; nothing reaches users
without review.

## Phase 2: Override-Table Entity Creation (Future)

**Prerequisite**: Phase 1 complete. Schema metadata proven stable across multiple firmware releases. Schema unit corrections resolved (no outstanding known
errors).

Replace the 47+ hardcoded `SensorEntityDescription` instances with:

1. **A declarative override table** mapping snapshot field paths to HA metadata (`device_class`, `state_class`, sign convention, entity category). The library's
   field metadata provides the base unit and datatype; the override table adds HA-specific semantics.
2. **A generic entity factory** that iterates the library's field metadata, applies overrides where present, and creates entities for fields that have an
   override entry. Fields without an override entry are not exposed -- they remain invisible until a maintainer explicitly reviews them and adds an override.

The override table reduces boilerplate for reviewed fields: the maintainer writes only the HA-specific semantics (device class, sign convention, etc.) and the
factory derives the rest from the library's field metadata. But no field is ever exposed without an explicit override entry. The integration never references
Homie node types or property IDs -- it operates entirely in terms of snapshot field paths and the library's field metadata.

### Trigger Criteria for Phase 2

Do not proceed to Phase 2 until:

- [ ] The Homie schema has had at least two firmware releases with no unit corrections
- [ ] Phase 1 validation logging has run in production and confirmed schema accuracy
- [ ] SPAN introduces schema versioning or a backwards-compatibility guarantee
- [ ] The rate of new properties is high enough that manual sensor additions are a meaningful maintenance burden

## Phase 3: Build-Time Dataclass Generation (Future)

**Prerequisite**: Phase 2 complete. Schema stable. Property additions are frequent.

Auto-generate `span-panel-api` snapshot dataclasses from the schema at build time:

1. A codegen script reads the schema from a reference panel (or saved fixture).
2. Outputs `models_generated.py` with typed frozen dataclasses matching the schema.
3. Manual `models.py` inherits from generated classes and adds derived fields (`dsm_state`, `current_run_config`, etc.).
4. `mypy` and IDE autocomplete continue working against concrete types.

This reduces the library-side work for new fields -- the snapshot dataclass picks up new fields automatically from the schema. However, generated fields still
require human review before they are exposed to the integration. The maintainer must verify the field's actual values against its declared unit and datatype,
then add an override entry in the integration's Phase 2 override table. The codegen eliminates the library boilerplate but does not bypass the review gate.

### Trigger Criteria for Phase 3

Do not proceed to Phase 3 until:

- [ ] Phase 2 override-table model is proven and the generic entity factory is stable
- [ ] SPAN releases firmware updates with new properties frequently enough to justify the codegen infrastructure
- [ ] The manual snapshot dataclass maintenance cost exceeds the codegen maintenance cost

## Cross-References

- [Architecture](architecture.md) -- system overview and data flow
- [Dynamic Enum Options](dynamic_enum_options.md) -- runtime enum handling and schema trust limitations (directly relevant to Phase 1 validation)
- [SPAN API Client Docs](https://github.com/spanio/SPAN-API-Client-Docs) -- upstream Homie schema documentation and changelog
