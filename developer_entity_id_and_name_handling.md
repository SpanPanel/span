# Entity IDs and Names — the rules

This document is the permanent statement of how this integration names entities and composes their entity IDs. It exists because the question has been
re-derived from scratch more than once. The rules below are the maintainer's intent; they outrank any single design document, review note or older comment. A
change to naming is checked against them first, and a change that cannot satisfy them is not made.

Every claim here is grounded in the code as of 2.1.0 (`naming.py`, `entity.py`, `sensor_base.py`, `sensor_circuit.py`, `switch.py`, `select.py`) and in Home
Assistant Core 2026.8 (`helpers/entity_registry.py`, `helpers/entity_platform.py`). When the code and this document disagree, one of them is wrong — fix it, do
not paper over it.

## 1. The five rules

| #      | Rule                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **R1** | **Recreate never offers an uncaused rename.** The base this integration supplies must reproduce an existing entity's ID when Home Assistant composes it under the entity-ID options matching how that install was built. Our own doing — two suffix spellings, a reworded label, a registry `name` an older release wrote — never produces an offer. An offer that reflects the user's own configuration (an area they assigned, a device they renamed, a circuit they renamed on the panel, `entity_id_parts` they chose) is legitimate. |
| **R2** | **The integration supplies only the flag-driven base.** `Circuit 15 power` or `Kitchen Outlets power`. Device and area prefixes are Core's, by the user's global `entity_id_parts` choice, and this integration does not exempt itself from that choice. No `entity_id` is preset, ever.                                                                                                                                                                                                                                                  |
| **R3** | **Circuit-numbers mode promises the circuit-number token stays stable across circuit renames** — not the surrounding prefix text, which is Core's.                                                                                                                                                                                                                                                                                                                                                                                        |
| **R4** | **The display name is the panel's, delivered as `original_name`.** The registry `name` field is the user's and is never written by this integration. The one write it makes to that field is to _clear_ a value a pre-2.1.0 release wrote there (§6).                                                                                                                                                                                                                                                                                     |
| **R5** | **Existing entity IDs never move.** Nothing moves an ID without the user pressing **Recreate entity IDs** and accepting the offer. Unique IDs never change with the naming style.                                                                                                                                                                                                                                                                                                                                                         |

A consequence worth stating outright: R1 does **not** mean bypassing `entity_id_parts` with a hard-coded ID. Where composition yields a different device or area
part than the entity has today, that is the user's configuration at work and the offer stands. The one-time attempt to add "legacy preset" exceptions for those
cases was withdrawn.

## 2. Three fields, three owners

Every entity has three naming-relevant values. Keeping them apart is the whole design.

| Value               | Owner           | What it is                                                                                            | Where it lives                                                                                       |
| ------------------- | --------------- | ----------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| **Display name**    | The panel       | `"{circuit name} {label}"` — `Kitchen Outlets Power`, `Bathroom Lights Breaker`                       | `_attr_name` → registry `original_name`. Same string in both naming modes.                           |
| **Object-ID base**  | The naming flag | `"{identifier} {suffix words}"` — `Kitchen Outlets power` or `Circuit 15 power`                       | `SpanPanelEntity._span_object_id_base` → `suggested_object_id` property → registry `object_id_base`. |
| **Registry `name`** | The user        | Whatever they typed in the entity's settings. Outranks everything in both display and ID composition. | Registry `name`. Read to detect an override; never written (except the §6 release).                  |

The display name and the base are decoupled **on purpose**: a label can be reworded without touching a single ID, and an ID can carry an old spelling while the
label is current.

## 3. How Home Assistant composes an ID (Core 2026.8)

This is Core's behaviour, cited so nobody has to re-read Core to trust the rest of the document.

### 3.1 Priority

`EntityRegistry._async_generate_entity_id` (`helpers/entity_registry.py`):

```text
name  >  suggested_object_id  >  object_id_base
```

- `name` (the user's registry name) — always prefixed with the device name.
- `suggested_object_id` — used **verbatim, not prefixed** with device or area. This is where a preset `entity_id` lands.
- `object_id_base` — prefixed with device (and area, if the user's settings include it) when `has_entity_name` is True. All of ours have it True.

The prefixing follows `self.settings.entity_id_parts`, defaulting to `(AREA, DEVICE, ENTITY)`. Device name is `device.name_by_user or device.name`; the area is
the entity's, falling back to the device's.

### 3.2 What the platform hands Core

`entity_platform._async_derive_object_ids`:

- If the entity preset `entity_id` (`internal_integration_suggested_object_id`), that becomes `suggested_object_id`. **We never do this.**
- Otherwise `entity.suggested_object_id` becomes `object_id_base`. The stock property returns the display name; ours returns the base (§4).

### 3.3 The stored row is refreshed on every add

`async_get_or_create` on an entity that already exists calls `_async_update_entity(..., object_id_base=..., suggested_object_id=...)`, and the update applies
any value that is not `UNDEFINED` and differs from the stored one — **including `None`**. So every reload rewrites the row's `object_id_base` from the current
base and clears a stale `suggested_object_id`. The row is never frozen at creation.

### 3.4 Recreate entity IDs regenerates from the stored row

`async_regenerate_entity_id(entry)` composes from `entry.name`, `entry.suggested_object_id`, `entry.object_id_base`, `entry.device_id`, `entry.area_id` — the
**stored row**, not the live entity. Two corollaries:

- A row whose `suggested_object_id` still holds a 2.0.8 preset reproduces that preset forever, whatever the entity now wants. That is exactly why 2.0.8 could
  not follow a rename (#252), and why a 2.0.8 install that _appears_ to run 2.1.0 but has not actually loaded it shows "No renamable entity IDs".
- A row whose `name` is set composes from that, device-prefixed, in both display and ID. That is why a 2.0.8 circuit-numbers install got friendly-name offers
  (§6).

## 4. What this integration does — one path, both modes

### 4.1 The identifier (the naming flag's half)

`sensor_circuit._resolve_circuit_identifier`, and the same branch inline in `switch.py` / `select.py`:

| Mode                                    | Identifier                                                | Source                                                                            |
| --------------------------------------- | --------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Circuit numbers (`use_circuit_numbers`) | `Circuit 15`, `Circuit 30 32` (every tab, sorted)         | `helpers.construct_circuit_identifier_from_tabs(circuit.tabs, circuit_id)`        |
| Friendly names, circuit has a name      | the panel's name, verbatim                                | `circuit.name`                                                                    |
| Friendly names, circuit unnamed         | `Solar` (PV), `EV Charger` (EVSE), else `Circuit 7` (tab) | `_unnamed_circuit_fallback` — never `None`, so two unnamed circuits never collide |
| Unmapped tab (either mode)              | `Unmapped Tab 32`                                         | `SpanUnmappedCircuitSensor._object_id_parts`                                      |

The identifier **always answers**. A `None` identifier lets Core compose from the label alone, which gives every unnamed circuit the same ID and leaves the
registry to disambiguate with `_2`, `_3` — the pre-2.1.0 behaviour that is now gone.

### 4.2 The suffix

Each entity names its canonical suffix:

- Circuit sensors: `id_builder.get_user_friendly_suffix(description key)` → `power`, `energy_consumed`, `energy_produced`, `energy_net`, `current`,
  `breaker_rating`.
- Switch: `"breaker"`, named outright (`get_user_friendly_suffix` has no switch entry).
- Select: the description key `"circuit_priority"`, verbatim (`get_user_friendly_suffix` would map it to `priority`, which is the unique-ID's spelling, not the
  entity-ID's).

`CIRCUIT_SUFFIX_MAPPING` in `id_builder` builds **unique IDs** and is closed. The tables in `naming.py` govern **entity IDs only**. Do not cross them.

### 4.3 The base — `naming.circuit_object_id_base(identifier, suffix, existing_entity_id)`

Returns `"{identifier} {suffix words}"`, where the words are decided in this order:

1. **The existing ID still names this circuit** (`..._{identifier_slug}_{form}` or `..._{identifier_slug}`): what follows the identifier settles both the form
   the ID carries and whether the preset builder omitted the suffix. They are one question, answered together — answering them separately produced
   `..._current_current_power` for a circuit named "Current" and `..._solar_power` for one named "Solar Power", both uncaused renames (R1).
2. **The existing ID no longer names this circuit** (renamed on the panel, #252): the form is read back from the _end_ of the ID by itself
   (`_existing_suffix_form`), and the omission is decided from the _new_ name. The name half follows the panel; the suffix half does not.
3. **No existing ID**: the noun-last wording from `NEW_ENTITY_ID_SUFFIX_WORDS` (`consumed energy`, matching the panel-level `main_meter_consumed_energy`).

`ENTITY_ID_SUFFIX_FORMS` lists every suffix form ever shipped (`consumed_energy` before the preset era, `energy_consumed` after; `current_power` and `power`).
**An entry is added only when another form is discovered to have shipped, never to introduce one.** An existing entity is offered whichever spelling it already
carries and never the other (R1).

Worked examples (friendly mode, `(DEVICE, ENTITY)` parts, device "Span Panel"):

| Circuit name (panel)    | Existing ID                                         | Suffix            | Base                              | Composed / offered                                              |
| ----------------------- | --------------------------------------------------- | ----------------- | --------------------------------- | --------------------------------------------------------------- |
| Kitchen Outlets         | none                                                | `energy_consumed` | `Kitchen Outlets consumed energy` | `sensor.span_panel_kitchen_outlets_consumed_energy`             |
| Kitchen Outlets         | `sensor.span_panel_kitchen_outlets_energy_consumed` | `energy_consumed` | `Kitchen Outlets energy consumed` | its own ID — no offer                                           |
| Bathroom Lights Renamed | `switch.span_panel_bathroom_lights_breaker`         | `breaker`         | `Bathroom Lights Renamed breaker` | `switch.span_panel_bathroom_lights_renamed_breaker`             |
| Solar Power             | `sensor.span_panel_solar_power_power`               | `power`           | `Solar Power power`               | its own ID                                                      |
| Solar Power             | `sensor.span_panel_solar_power`                     | `power`           | `Solar Power`                     | its own ID                                                      |
| Power                   | `sensor.span_panel_power_power`                     | `power`           | `Power power`                     | its own ID (a name that _is_ the suffix word keeps both halves) |

Circuit-numbers mode is the same table with `Circuit 15` in the identifier column — and a rename on the panel changes nothing in it (R3).

### 4.4 Delivery to Core

`SpanPanelEntity` (`entity.py`) carries `_span_object_id_base: str | None = None` and overrides the `suggested_object_id` property to return it when set, or
`super().suggested_object_id` (the display name) when not. Core's `type.__getattribute__` guard covers the `name` property only, so this override is supported.
Circuit sensors, the switch and the select set the base at construction. Every other entity leaves it `None` and composes from its label as it always has.

`entity_id` is **never** preset. Nothing in this integration assigns `self.entity_id` or `_attr_entity_id`.

### 4.5 Who composes without a base

- **Sub-device circuit sensors** (an EVSE feed circuit's sensors, `_is_sub_device`): no base. Core composes from the label on the charger's device, so the ID
  names the charger alone — `sensor.<charger>_power` — like the charger's other sensors. A feed sensor created before this route keeps its old ID until Recreate
  is pressed, and is then offered the charger-named one (a legitimate offer: the device part is Core's).
- **Everything not a circuit entity** (panel, BESS, MID, EVSE, PV, binary sensors, adopted properties): stock behaviour, label-composed.

### 4.6 Legacy `use_device_prefix`

Stored, preserved by the options flow, and read by `get_current_naming_pattern`, but it **cannot keep the device out of a composed ID** — Core includes the
device unless the user's own `entity_id_parts` excludes it. An install predating the option, whose circuit IDs carry no device half, is offered one on Recreate.
That is Core composing under the user's settings, and it is documented in the README and CHANGELOG rather than special-cased.

## 5. Name sync — what "phase 1" and "phase 2" mean, and what they mean now

The terms come from the pre-2.1.0 design and are still used in conversation, so here is what each was and what it is today.

### 5.1 Before 2.1.0 (the two-phase scheme, for context only)

- **Phase 1 — construction.** `_attr_name` was set from the _naming flag_ (`Circuit 15 Power` in circuit-numbers mode) so that Core would derive a flag-shaped
  `object_id_base`, and `entity_id` was **preset** by a builder, which Core stored as `suggested_object_id`. A new entity displayed the flag name until phase 2.
- **Phase 2 — first coordinator update, and every rename after.** Circuit-numbers mode **wrote the registry `name`** with the panel's name to fix the display.
  That put the panel name in the user's field, which outranks everything in ID generation — so Recreate proposed a friendly-name ID for every circuit-numbered
  entity, and a rename in the SPAN app could never reach the preset `suggested_object_id` (#252). Friendly mode's phase 2 was idempotent (same string) and wrote
  nothing.

### 5.2 In 2.1.0 the phases collapse to one

There is one name path, in both modes:

1. **Construction** (`sensor_base.py`, `switch.py`, `select.py`): `_attr_name = "{circuit name} {label}"` — the _panel's_ name, unconditionally, existing entity
   or not. It reaches the registry as `original_name`. The base (§4.3) is computed at the same time and handed to Core through `suggested_object_id`. If an
   existing row carries a `name` a pre-2.1.0 release wrote, it is released (§6).
2. **Every coordinator update** (`_handle_coordinator_update` / `_sync_circuit_name`): the entity compares the panel's current circuit name with the one it was
   built from.
   - If the registry `name` is set, the user has overridden the name: the panel's rename is **noted and ignored** — theirs outranks it, and a reload would
     change nothing on screen.
   - If the entity was **not** in the registry at construction (`_previous_circuit_name is _NAME_UNSET`), the first update requests one reload. This is the
     surviving trace of "phase 2": a freshly created entity's registry row is settled by that reload.
   - If the name **changed**, the entity requests a reload (`coordinator.request_reload()`).
3. **Reload** (`coordinator.py`, end of the update cycle): the entry is torn down and rebuilt. `original_name` is only written when an entity is added, so a
   rebuild is the only way to refresh it — that is why a rename costs a reload rather than a registry write. The rebuild also recomputes the base with the new
   circuit name and, per §3.3, rewrites the row's `object_id_base`.

The rename therefore reaches:

| What                      | Friendly names                                    | Circuit numbers                                                                                     |
| ------------------------- | ------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Display name              | new name, after the reload                        | new name, after the reload (`Span Panel Kitchen Outlets Power`, device-prefixed like friendly mode) |
| Entity ID                 | unchanged (R5)                                    | unchanged (R5)                                                                                      |
| Row `object_id_base`      | `Kitchen Outlets Renamed power`                   | `Circuit 15 power` — unchanged                                                                      |
| Recreate entity IDs offer | `sensor.span_panel_kitchen_outlets_renamed_power` | its own ID — no offer (R3)                                                                          |
| Registry `name`           | untouched                                         | untouched                                                                                           |

Settability changes (a circuit becoming or ceasing to be controllable) are **not** watched here; `SpanPanelCoordinator._check_settability_change` does that,
because the "gains a control" edge happens on circuits that have no entity to see it.

## 6. Releasing the registry `name` a pre-2.1.0 release wrote

`naming.release_registry_name_written_by_older_release(registry, entity_id, circuit_name, description_names)` runs once per existing circuit entity at
construction. It clears the registry `name` **only** if it equals `"{circuit_name} {label}"` for the description's current label _or any label it has carried
before_ (`SpanPanelCircuitsSensorEntityDescription.legacy_names`: `Energy Consumed` / `Energy Produced` / `Energy Net` beside the current `Consumed Energy`
etc.). Any other value is the user's and is left exactly where it is. The switch releases `"{name} Breaker"`, the select `"{name} {description name}"`.

This is the one write this integration ever makes to the user's field, and it is a hand-back, not a claim. Miss a release site and that platform's Recreate
stays poisoned: under composition a stale `name` composes _with the area_, so a 2.0.8 name on a panel in "Basement" would propose
`sensor.basement_span_panel_kitchen_outlets_consumed_energy`.

## 7. Offers that are legitimate — and the one that is not fully avoidable

Recreate offers a different ID only where the user's own configuration composes one. Each of these is documented for users in README and CHANGELOG:

| Situation                                                                    | Offer                 | Cause                                                                                             |
| ---------------------------------------------------------------------------- | --------------------- | ------------------------------------------------------------------------------------------------- |
| Circuit renamed on the panel, friendly mode                                  | the new name's ID     | user renamed it                                                                                   |
| Panel device in an area, `entity_id_parts` includes AREA (the default)       | area-prefixed ID      | user assigned the area                                                                            |
| Panel device renamed by the user (`name_by_user`)                            | ID carrying that name | user renamed the device                                                                           |
| Sensor on a SPAN Drive feed circuit                                          | charger-named ID      | device part is Core's                                                                             |
| Second panel whose generated device name is "Span Panel 2"                   | `span_panel_2_…`      | device part is Core's                                                                             |
| Install predating `use_device_prefix`, circuit IDs with no device half       | device-prefixed ID    | device part is Core's                                                                             |
| Unnamed circuit in friendly mode whose ID carries `single_circuit`/`power_2` | `circuit_7…`          | **the one uncaused offer** — no computed identifier reproduces both eras; accepted and documented |

Anything not in this table that Recreate offers on an unchanged install is a bug against R1.

## 8. Things that must stay true (checklist for any change touching naming)

- [ ] No entity presets `entity_id`. `Grep` for `entity_id =` and `_attr_entity_id` in platform code finds nothing.
- [ ] The registry `name` is never assigned a value; the only `async_update_entity(..., name=...)` call is the `None` in
      `release_registry_name_written_by_older_release`.
- [ ] `_attr_name` is the panel's name in both modes; nothing branches on `use_circuit_numbers` to pick a display name.
- [ ] The base is computed from `(identifier, canonical suffix, existing_entity_id)` and nothing else — never from the display label.
- [ ] `ENTITY_ID_SUFFIX_FORMS` gains an entry only for a form found to have shipped; `NEW_ENTITY_ID_SUFFIX_WORDS` and it stay in one-to-one correspondence
      (`test_every_form_table_entry_has_a_new_wording_and_vice_versa`).
- [ ] A reworded description label adds the old label to `legacy_names` so §6 still recognises what 2.0.8 wrote.
- [ ] Unique IDs are untouched by any of this; `CIRCUIT_SUFFIX_MAPPING` is closed.
- [ ] `tests/test_naming.py` and `tests/test_recreate_entity_ids.py` pass. The latter drives a real `EntityPlatform` with `entity_id_parts` fixed, so a
      composition regression fails there rather than in a unit test of a builder.
- [ ] Run the `entity-id-auditor` agent after editing `id_builder.py`, `entity_resolver.py`, `naming.py` or any circuit platform file.

## 9. Diagnosing "Recreate offered nothing" on a live install

1. Confirm the running code: `manifest.json` version and the presence of `custom_components/span_panel/naming.py`. A 2.0.8 install with 2.1.0 selected in HACS
   but not yet loaded shows exactly this symptom, because its rows still hold the 2.0.8 preset in `suggested_object_id` (§3.4).
2. Read the entity's row in `.storage/core.entity_registry`: on 2.1.0 after at least one reload, `suggested_object_id` is `null` and `object_id_base` reads the
   base (`Bathroom Lights Renamed breaker`). A non-null `suggested_object_id` means the entity has not been re-added by 2.1.0 code.
3. Check `name` on the row: a non-null value is a user override (or an unreleased 2.0.8 write — compare against §6's patterns) and outranks the base.
4. Confirm the rename-triggered reload happened: the log carries `Auto-sync detected circuit name change from '…' to '…'` followed by the entry reload.
5. Only then look at `circuit_object_id_base` with the row's exact `entity_id`, identifier and suffix — `tests/test_naming.py` shows how to call it directly.

## 10. Where the history lives

The design trail (issue #252 write-up, the 2026-08-22 spec and plan, the 2026-08-26 composition-route review) lives in the `SpanPanel_Docs` workspace, not in
this repository. It records how the rules were arrived at and the routes that were rejected. It is history; this document is the rule.
