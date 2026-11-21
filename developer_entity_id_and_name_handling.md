# Entity IDs and Names — the rules

This document is the permanent statement of how this integration names entities and composes their entity IDs. It exists because the question has been
re-derived from scratch more than once. The rules below are the maintainer's intent; they outrank any single design document, review note or older comment. A
change to naming is checked against them first, and a change that cannot satisfy them is not made.

Every claim here is grounded in the code as of 2.3.0 (`naming.py`, `circuit_object_ids.py`, `circuit_number_ids.py`, `entity.py`, `sensor_base.py`,
`sensor_circuit.py`, `switch.py`, `select.py`, `circuit_device_conversion.py`) and in Home Assistant Core 2026.11 (`helpers/entity_registry.py`,
`helpers/entity_platform.py`, `helpers/device_registry.py`). When the code and this document disagree, one of them is wrong — fix it, do not paper over it.

Since 2.3.0 each circuit has a device of its own, a child of the panel's device and named by the circuit. The circuit's name moved from every entity's name and
ID base to that device, and R1, R2 and R3 were amended on 2026-11-14 to say what follows from it.

## 1. The five rules

| #      | Rule                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **R1** | **Recreate never offers an uncaused rename.** That is the rule in circuit-names mode: the base this integration supplies must reproduce an existing entity's ID when Home Assistant composes it under the entity-ID options matching how that install was built. Our own doing — two suffix spellings, a reworded label, a registry `name` an older release wrote — never produces an offer. An offer that reflects the user's own configuration (an area they assigned, a device they renamed, a circuit they renamed on the panel, `entity_id_parts` they chose) is legitimate. In circuit-numbers mode the creation preset reproduces the circuit-number ID when the entity is created. Recreate afterwards offers the circuit's name, which reflects the name the user gave the circuit in the SPAN app (R3). One further exception: an entity that keeps an area of its own on a circuit whose areas did not move (the conversion's area rule 3, §6.2) is offered its label alone, matching its displayed name. |
| **R2** | **The integration supplies only the suffix as the base,** such as `power`, in the form the existing ID carries (R1); the circuit's name comes from its device. Device and area prefixes are Core's, by the user's global `entity_id_parts` choice, and this integration does not exempt itself from that choice. One exception: in circuit-numbers mode, an entity the integration creates has its `entity_id` preset to what Core would compose with the circuit number in the device's place. The preset is never supplied again, so Core clears it at the entity's next add.                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| **R3** | **Circuit-numbers mode promises a circuit-number ID for every circuit entity the integration creates.** Recreate afterwards offers the circuit's name, aligning the ID with the displayed name as Home Assistant intends.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| **R4** | **The display name is the panel's,** delivered as the name of the circuit's device, with each entity's label as its `original_name`. The registry `name` field is the user's and is never written by this integration. The two writes it makes to that field are to _clear_ a value a pre-2.1.0 release wrote there (§6.1) and, once, in the conversion to circuit devices, to trim the circuit's name from the front of a name that began with it (§6.2).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| **R5** | **Existing entity IDs never move.** Nothing moves an ID without the user pressing **Recreate entity IDs** and accepting the offer. Unique IDs never change with the naming style.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |

A consequence worth stating outright: R1 does **not** mean bypassing `entity_id_parts` with a hard-coded ID. Where composition yields a different device or area
part than the entity has today, that is the user's configuration at work and the offer stands. The one-time attempt to add "legacy preset" exceptions for those
cases was withdrawn. R2's creation preset is not such a bypass: it is composed under the user's `entity_id_parts` (§4.7), and it is made once.

For a circuit's own device and the entities on it, the two modes differ in one thing only: the entity ID a new entity is created with. The device's name, the
displayed names, the base and what Recreate offers are the same in both. Outside that, the naming flag still chooses the suffix in the name of a charger's
device and of an additional solar inverter's (§4.1), which reaches every entity on those devices, a charger feed circuit's sensors among them (§4.5).

## 2. Four values and their owners

A circuit entity has four naming-relevant values. Keeping them apart is the whole design.

| Value               | Owner                         | What it is                                                                                                                                                 | Where it lives                                                                                                                                                                                                                     |
| ------------------- | ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Circuit's name**  | The panel                     | `Kitchen Outlets`, the circuit's name in the SPAN app, or the fallback an unnamed circuit gets (§4.1). Same string in both naming modes.                   | The `name` of the circuit's device, from `helpers.circuit_device_name` through `util.circuit_device_info`. A name the user gives that device in Home Assistant (`name_by_user`) is theirs, and Core reads it ahead of the panel's. |
| **Label**           | The integration               | What the entity measures or controls: `Power`, `Consumed Energy`, `Breaker`, `Circuit Priority`. Same string in both naming modes.                         | `_attr_name` → registry `original_name`.                                                                                                                                                                                           |
| **Object-ID base**  | The ID the entity already has | The suffix words alone, `power` or `energy consumed`, in the form the existing ID carries, or empty where that ID stops at the circuit's name (§4.3).      | `SpanPanelEntity.suggested_object_id`, worked out from `_span_id_base_source` as Core asks → registry `object_id_base`.                                                                                                            |
| **Registry `name`** | The user                      | Whatever they typed in the entity's settings. It takes the label's place in the displayed name, and outranks both a preset and the base in ID composition. | Registry `name`. Read, never written, except by the §6.1 release and the conversion's trim (§6.2).                                                                                                                                 |

Home Assistant composes what is displayed from the two devices and the label: `Span Panel Kitchen Outlets Power`. The circuit's name is in no entity's name and
in no base.

The label and the base are decoupled **on purpose**: a label can be reworded without touching a single ID, and an ID can carry an old spelling while the label
is current.

## 3. How Home Assistant composes an ID (Core 2026.11)

This is Core's behavior, cited so nobody has to re-read Core to trust the rest of the document.

### 3.1 Priority and parts

`EntityRegistry._async_generate_entity_id` (`helpers/entity_registry.py`), which composes through `_async_get_full_entity_name`:

```text
name  >  suggested_object_id  >  object_id_base
```

- `name` (the user's registry name) — takes the entity part, and is prefixed with the area and device parts like a base.
- `suggested_object_id` — used **verbatim, not prefixed** with any part, while the row has no `name`. This is where a preset `entity_id` lands.
- `object_id_base` — the entity part, prefixed with the other parts when `has_entity_name` is True. All of ours have it True.

The parts follow `self.settings.entity_id_parts`, defaulting to `(AREA, PARENT_DEVICE, DEVICE, ENTITY)`. A format the user saves must keep the device and entity
parts and may leave out the others. For an entity on a circuit's device:

- **DEVICE** is the circuit device's `name_by_user or name`. An entity with an area of its own gets neither device part: Core names an entity by its own area in
  place of its device.
- **PARENT_DEVICE** is the panel device's `name_by_user or name`, and is included only while the circuit's device has no area of its own.
- **AREA** is the entity's own area, else the circuit device's, else the panel device's.

With the default parts, `power` on the device `Kitchen Outlets` under `Span Panel` composes as:

| Where an area is set               | Composed ID                                        |
| ---------------------------------- | -------------------------------------------------- |
| Nowhere                            | `sensor.span_panel_kitchen_outlets_power`          |
| On the panel's device, "Basement"  | `sensor.basement_span_panel_kitchen_outlets_power` |
| On the circuit's device, "Kitchen" | `sensor.kitchen_kitchen_outlets_power`             |
| On the entity itself, "Kitchen"    | `sensor.kitchen_power`                             |

### 3.2 What the platform hands Core

`entity_platform._async_derive_object_ids`:

- If the entity preset `entity_id` (`internal_integration_suggested_object_id`), that becomes `suggested_object_id`, and no base is recorded on that add. **We
  do this once per entity, when circuit-numbers mode creates it (§4.7), and at no other time.**
- Otherwise the entity's `suggested_object_id` property becomes `object_id_base`. The stock property returns the display name; ours returns the base (§4).

### 3.3 The stored row is refreshed on every add

`async_get_or_create` on an entity that already exists calls `_async_update_entity(..., object_id_base=..., suggested_object_id=...)`, and the update applies
any value that is not `UNDEFINED` and differs from the stored one — **including `None`**. So every reload rewrites the row's `object_id_base` from the current
base and clears a stale `suggested_object_id`. The row is never frozen at creation.

That is how the creation preset is spent: the entity's next add supplies no preset, so `suggested_object_id` returns to `None` and `object_id_base` takes the
suffix.

### 3.4 Recreate entity IDs regenerates from the stored row

`async_regenerate_entity_id(entry)` composes from `entry.name`, `entry.suggested_object_id`, `entry.object_id_base`, `entry.device_id`, `entry.area_id` — the
**stored row**, not the live entity. Two corollaries:

- A row whose `suggested_object_id` still holds a preset reproduces that preset forever, whatever the entity now wants. That is exactly why 2.0.8, which preset
  on every load, could not follow a rename (#252), and why a 2.0.8 install that _appears_ to run 2.1.0 but has not actually loaded it shows "No renamable entity
  IDs". It is also why the creation preset is supplied once: supplied on every load, it would exempt the entity from the user's entity-ID format for good.
- A row whose `name` is set composes from that, with the device parts in front. That is why a 2.0.8 circuit-numbers install got circuit-name offers (§6.1).

## 4. What this integration does — one path, both modes

### 4.1 The circuit's device, and the identifier (the naming flag's half)

Each circuit's entities sit on the circuit's own device: a child of the panel's device (`ChildDeviceInfo` from `util.circuit_device_info`), identified by
`{serial}_circuit_{circuit_id}` (`id_builder.build_circuit_device_identifier`) and named by `helpers.circuit_device_name`, the one helper everything that
registers the device calls. The name is the same in both naming modes:

| Circuit                                 | Device name                                      | Source                                                                     |
| --------------------------------------- | ------------------------------------------------ | -------------------------------------------------------------------------- |
| A meter outside the panel (a remote CT) | `Remote CT 1`                                    | `helpers.outside_meter_label(circuit_id)`                                  |
| Named in the SPAN app                   | the panel's name, verbatim                       | `circuit.name`                                                             |
| Unnamed solar (PV) circuit              | `Solar`                                          | `circuit.device_type == "pv"`                                              |
| Any other unnamed circuit               | `Circuit 7`, `Circuit 30 32` (every tab, sorted) | `helpers.construct_circuit_identifier_from_tabs(circuit.tabs, circuit_id)` |

The name **always answers**. A device with no name would leave Core composing from the label alone, which gives every unnamed circuit the same ID and leaves the
registry to disambiguate with `_2`, `_3` — the pre-2.1.0 behavior that is now gone.

The naming flag no longer decides a circuit's name. It still chooses the suffix in two other devices' names:

- **A charger's device**: the fed circuit's name, or in circuit-numbers mode the charger's serial (`entity_resolver.resolve_evse_display_suffix`).
- **An additional solar inverter's device**: its feed circuit's name, or that circuit's breaker positions in circuit-numbers mode and where the circuit has no
  name (`helpers.resolve_pv_display_suffix`, with `helpers.resolve_pv_display_suffixes` keeping several inverters' names apart).

The suffix is part of the device's name, so the displayed names of the entities on those two devices, and the IDs Core composes for them, differ by mode.

For a circuit, the flag resolves an _identifier_, by `sensor_circuit._resolve_circuit_identifier` and the same branch inline in `switch.py` / `select.py`, which
is used for two things only: an existing ID is read back against it (§4.3), and in circuit-numbers mode it stands in the device's place in the creation preset
(§4.7).

| Mode                                    | Identifier                                           | Source                                                                     |
| --------------------------------------- | ---------------------------------------------------- | -------------------------------------------------------------------------- |
| Either, for a meter outside the panel   | `Remote CT 1`                                        | `helpers.circuit_device_name(circuit, circuit_id)`                         |
| Circuit numbers (`use_circuit_numbers`) | `Circuit 15`, `Circuit 30 32` (every tab, sorted)    | `helpers.construct_circuit_identifier_from_tabs(circuit.tabs, circuit_id)` |
| Circuit names                           | the name of the circuit's device, as the table above | `helpers.circuit_device_name(circuit, circuit_id)`                         |
| Circuit names, unnamed charger circuit  | `EV Charger`, for its sensors only                   | `sensor_circuit.UNNAMED_CHARGER_CIRCUIT_IDENTIFIER`                        |

In circuit-names mode the identifier is the device's name, from the same helper, so a change to how a device is named cannot leave a platform reading IDs
against the old rule (`test_circuit_names_mode_reads_ids_back_against_the_device_s_name`). The last row is the one exception: the sensors of an unnamed charger
circuit were built from "EV Charger" while its switch, its select and now its device are named by its tabs. It is kept only as the name those sensors' IDs were
built from (§7), and names no device.

### 4.2 The suffix

Each entity names its canonical suffix:

- Circuit sensors: `id_builder.get_user_friendly_suffix(description key)` → `power`, `energy_consumed`, `energy_produced`, `energy_net`, `current`,
  `breaker_rating`.
- Switch: `"breaker"`, named outright (`get_user_friendly_suffix` has no switch entry).
- Select: the description key `"circuit_priority"`, verbatim (`get_user_friendly_suffix` would map it to `priority`, which is the unique-ID's spelling, not the
  entity-ID's).

`CIRCUIT_SUFFIX_MAPPING` in `id_builder` builds **unique IDs** and is closed. The tables in `naming.py` govern **entity IDs only**. Do not cross them.

### 4.3 The base — `circuit_object_ids.circuit_object_id_base` and `naming.circuit_object_id_suffix`

The base is the suffix alone: the suffix words, or an empty string where the ID leaves them out, and Core composes the circuit's name from the device. It is
never the circuit's name.

`device_name` below is the name Core composes from, as `util.effective_circuit_device_name` gives it: the user's own name for the circuit's device where they
set one, otherwise the name the device takes on this load, which after a rename on the panel is not yet the one the registry holds. `id_identifier` is §4.1's.

**An ID Core composes today is given the base that composes it.** `circuit_object_ids.circuit_object_id_base` composes the ID for each wording the suffix has
shipped with, and for the empty base, under the user's entity-ID format, the areas and the names as they stand (`circuit_object_ids.compose_circuit_object_id`,
§4.7's composer with `device_name` in the device's place). The wording that spells the ID the entity has is its base, as it stands or under the number Core
appends where an ID is taken. Nothing is assumed about where in the ID the entity's part sits, so this holds for any format the user saves, with the entity
before the device or a part after it.

**Any other ID is read by `naming.circuit_object_id_suffix(id_identifier, device_name, suffix, existing_entity_id, device_then_entity=...)`.** No wording
reproduces an ID an older release preset, the circuit-number preset of a named circuit, or an ID composed before a circuit, a device or an area was renamed or
the format changed. Those are read against two names, and the words are decided in this order:

1. **The existing ID names the device** (`..._{device_slug}_{form}` or `..._{device_slug}`): what follows the name settles both the form the ID carries and
   whether the ID left the suffix out. They are one question, answered together — answering them separately produced `..._current_current_power` for a circuit
   named "Current" and `..._solar_power` for one named "Solar Power", both uncaused renames (R1). An ID that ends on the name stops there, the suffix left out,
   only where that reading accounts for more of its tail than the form at its end does, or for as much and the device's name leaves the suffix out (below).
2. **The existing ID names the identifier but not the device** (a circuit-number ID such as `..._circuit_15_power`, or a circuit-names ID whose device the user
   has since renamed): what follows the identifier settles the form only, and where nothing follows it the end of the ID does. Whether the suffix is left out is
   decided from `device_name`, as it is for a new entity, because the offer is composed from the device. That is what makes the offer in circuit-numbers mode
   the ID the circuit has under circuit names: a circuit named "Solar Power" on `..._circuit_15_power` is offered `..._solar_power`.
3. **The existing ID names neither** (renamed on the panel, #252): the form is read back from the _end_ of the ID by itself (`_existing_suffix_form`), and the
   omission is decided from `device_name`. The name half follows the panel; the suffix half does not. An ID that is the form alone ends on it too. Under
   `(PARENT_DEVICE, DEVICE, ENTITY)`, which has no area part, the label-only ID an entity with an area of its own accepts is the form alone, such as
   `sensor.energy_consumed`. Once the area is set on the circuit's device and the entity's own is cleared, it is offered `sensor.refrigerator_energy_consumed`.
   Read as ending on no form, it was offered `sensor.refrigerator_consumed_energy`.
4. **No existing ID**: the noun-last wording from `NEW_ENTITY_ID_SUFFIX_WORDS` (`consumed energy`, matching the panel-level `main_meter_consumed_energy`),
   omitted where `device_name` already ends with it.

Where both names read the ID, the reading that accounts for more of its tail wins, and the device wins a tie. The trailing `power` of `..._circuit_15_power`
also reads as an ID that stops at a device named "Power"; the tabs account for more of it, so it is the tabs' ID.

Under rules 2 to 4 the suffix is left out only where the device's name ends with the suffix words after another word: "Solar Power" composes `..._solar_power`.
A device named exactly "Power" repeats nothing and keeps both halves, `..._power_power`.

Those four rules read an ID's tail, which is where the entity's part is under a format that ends with the device and then the entity, as the default does and as
every ID a release preset is shaped. `device_then_entity` says whether the user's format ends that way. Where it does not:

- an ID that ends on a name is not read as stopping there, because under `(ENTITY, DEVICE)` every composed ID ends on the device's name. Read so,
  `sensor.power_kitchen_outlets` gave the empty base and each entity of the circuit was offered the device's bare ID;
- an ID that spells a name and then a form is still read by rules 1 and 2;
- otherwise the form is the one the ID spells as whole words anywhere outside the name it carries, and the omission is decided from `device_name`.

**An ID Core numbered is read past the number.** Where the ID Core composed is taken, as it is for every circuit after the first of one name, Core appends `_2`,
`_3` and on, so the ID ends on the number and none of the readings above finds a name or a form there. Where the ID as it stands gives no reading, it is read
again without the number (`naming.without_core_number`). On a circuit renamed "Beer Fridge" in the SPAN app, `sensor.span_panel_refrigerator_energy_consumed_2`
keeps `energy consumed` and is offered `sensor.span_panel_beer_fridge_energy_consumed`. Read only as it stands, it spelled no wording and was offered
`sensor.span_panel_beer_fridge_consumed_energy`. The ID as it stands is read first because a name can end on a number of its own, and under a format that ends
with the device every ID ends on the device's name: under `(ENTITY, DEVICE)` a circuit named "Solar Power 2" has `sensor.current_power_solar_power_2`, which
without its `2` would be read for the name's `power`.

**An entity with an area of its own never has an empty base.** Every omission above rests on Core composing the device's name in front of the base, and Core
names neither device for an entity that has an area of its own (§3.1): it composes the area and the base. So for such an entity the empty base is not tried, and
each wording is composed twice. It is composed first from that area, as Core composes the entity now, so that an ID Core composes today, such as a label-only ID
the entity has accepted, is offered nothing. Under `(DEVICE, ENTITY)` a circuit named "Current" in an area of its own therefore keeps `sensor.current_power`,
its label alone in the `current_power` wording, though its device once composed that ID from `power`. It is composed then with the device in front, as Core
composed it before it had the area, which reads an ID from then exactly, number and all, where reading its words would give another wording, as it does for one
whose panel's name runs into the suffix: under `(PARENT_DEVICE, ENTITY, DEVICE)` a panel named "Shop Current" makes `sensor.shop_current_power_kitchen_outlets`
spell `current_power`, though it was composed from `power`. In the area Garage it is offered `sensor.power`.

Where neither composes the ID, it is read by `circuit_object_id_suffix`, told with `device_in_front=False` that no device is in front, and nothing is left out.
The words are the form the ID spells after the name, or failing that at its end. A "Solar Power" sensor on `sensor.span_panel_solar_power` in the area Backyard
is offered `sensor.backyard_power`, its label alone, which is R1's own-area exception. Left out, the offer was `sensor.backyard`, and under a format with no
area part Core's fallback of the platform and the unique ID.

**An ID that ends on the device's name and on a form alike is read for the form.** Rule 1's stop is ranked against the form of the suffix at the ID's end by the
tail each accounts for (`naming._stops_at_the_device`), as the device's reading is ranked against the identifier's. The stop wins where no form ends the ID or a
shorter one does, as `power` does inside `..._solar_power`. A longer form wins over it, as `current_power` does on a device named "Power". Where the two are the
same characters, the stop wins only where the device's name leaves the suffix out. That decides a circuit named for what its entity measures, "Power", "Energy"
or "Consumed Energy". The label-only ID such an entity accepts, `sensor.garage_power` under the default format or `sensor.power` under one with no area part,
stays its ID once its area is cleared or set on the circuit's device as the README advises. Read as stopping at the name, it was offered
`sensor.span_panel_power`, or `sensor.kitchen_power` after the README's remedy. Read for its form, it is offered `sensor.span_panel_power_power`, the ID the
circuit had and the one a new entity on that device gets. A circuit renamed "Power" in the SPAN app is read the same way: `sensor.span_panel_solar_power` is
offered `sensor.span_panel_power_power`, the form at its end with the omission decided from the new name, as rule 3 gives every other rename. "Current Power"
leaves `power` out, so its `sensor.current_power` stops at the name, the empty base a new entity there gets. The reading is a function of the ID and the names
alone. Where Core composes such an ID today from the device's name with the suffix left out, as it composes `sensor.garage_power` while the device is in Garage,
the first rule above gives it the empty base and nothing is offered, so the base follows the device's area and the panel's, and the names of the areas and their
floors (§5.3).

An offer the user accepts is an ID Core composes, so it is given the base it was composed from and the next restart offers nothing more
(`test_an_accepted_offer_reads_back_to_the_same_suffix`, and under reordered formats `test_an_id_composed_under_another_format_reads_back_to_its_base`).

`ENTITY_ID_SUFFIX_FORMS` lists every suffix form ever shipped (`consumed_energy` before the preset era, `energy_consumed` after; `current_power` and `power`).
**An entry is added only when another form is discovered to have shipped, never to introduce one.** An existing entity is offered whichever spelling it already
carries and never the other (R1).

Worked examples (circuit-names mode, `(PARENT_DEVICE, DEVICE, ENTITY)` parts, panel device "Span Panel", each circuit's device named by the circuit):

| Circuit name (panel)    | Existing ID                                         | Suffix            | Base              | Composed / offered                                              |
| ----------------------- | --------------------------------------------------- | ----------------- | ----------------- | --------------------------------------------------------------- |
| Kitchen Outlets         | none                                                | `energy_consumed` | `consumed energy` | `sensor.span_panel_kitchen_outlets_consumed_energy`             |
| Kitchen Outlets         | `sensor.span_panel_kitchen_outlets_energy_consumed` | `energy_consumed` | `energy consumed` | its own ID — no offer                                           |
| Bathroom Lights Renamed | `switch.span_panel_bathroom_lights_breaker`         | `breaker`         | `breaker`         | `switch.span_panel_bathroom_lights_renamed_breaker`             |
| Solar Power             | `sensor.span_panel_solar_power_power`               | `power`           | `power`           | its own ID                                                      |
| Solar Power             | `sensor.span_panel_solar_power`                     | `power`           | empty             | its own ID                                                      |
| Power                   | `sensor.span_panel_power_power`                     | `power`           | `power`           | its own ID (a name that _is_ the suffix word keeps both halves) |

Circuit-numbers mode, same parts, the circuit on tab 15:

| Circuit name (panel) | Existing ID                                                     | Suffix            | Base                        | Composed / offered                                                             |
| -------------------- | --------------------------------------------------------------- | ----------------- | --------------------------- | ------------------------------------------------------------------------------ |
| Kitchen Outlets      | none                                                            | `power`           | `power`, unused on this add | created as `sensor.span_panel_circuit_15_power`, by the creation preset (§4.7) |
| Kitchen Outlets      | `sensor.span_panel_circuit_15_power`                            | `power`           | `power`                     | `sensor.span_panel_kitchen_outlets_power` (R3)                                 |
| Kitchen Outlets      | `sensor.span_panel_circuit_15_energy_consumed`                  | `energy_consumed` | `energy consumed`           | `sensor.span_panel_kitchen_outlets_energy_consumed`                            |
| Solar Power          | `sensor.span_panel_circuit_15_power`                            | `power`           | empty                       | `sensor.span_panel_solar_power`                                                |
| Kitchen Outlets      | `sensor.span_panel_kitchen_outlets_power` (the offer, accepted) | `power`           | `power`                     | its own ID                                                                     |

### 4.4 Delivery to Core

`SpanPanelEntity` (`entity.py`) carries `_span_id_base_source: CircuitIdBaseSource | None = None` and overrides the `suggested_object_id` property. Where a
source is set, the property works the base out from it as Core asks (`circuit_object_ids.circuit_object_id_base_now`), from the entity's registry row as it
stands. Where none is, it returns `super().suggested_object_id` (the display name). An empty base is a base: Core then composes the ID from the other parts
alone. Core's `type.__getattribute__` guard covers the `name` property only, so this override is supported. Circuit sensors, the switch and the select set the
source at construction, through `SpanPanelEntity._circuit_id_base_source`, which also records it with the entry for the listener (§5.3). Every other entity
leaves it `None` and composes from its label as it always has.

Core reads `suggested_object_id` only as it adds an entity, which is as the entry sets up. So the base Core writes at an add is the one the row's ID calls for
at that moment, not one fixed when the entity was built. An edit to a live entity's ID adds nothing. Home Assistant 2026.11 removes a custom integration's
entity and adds it again wherever a class in its MRO defines an add or remove hook and neither that class nor a subclass defines `async_entity_id_changed`
(`_entity_class_requires_readd` in Core's `helpers/entity.py`, a compatibility path Core says can be removed in 2027.11). Each class here that defines such a
hook defines `async_entity_id_changed` beside it, so Core changes the ID in place, which `tests/test_entity_ids_change_in_place.py` holds for every entity
class. In place Core writes no base, and the listener keeps it current (§5.3).

`entity_id` is preset in one case only: a circuit entity being created in circuit-numbers mode (§4.7). Nothing else in this integration assigns
`self.entity_id`, and nothing assigns `_attr_entity_id`.

### 4.5 Who composes without a base

- **Sub-device circuit sensors** (an EVSE feed circuit's sensors, `_is_sub_device`): no base, and no creation preset in either mode. They sit on the charger's
  device, not on a circuit's, so Core composes from the label on that device and the ID names the charger alone — `sensor.<charger>_power` — like the charger's
  other sensors. A feed sensor created before this route keeps its old ID until Recreate is pressed, and is then offered the charger-named one (a legitimate
  offer: the device part is Core's). The charger's device name carries the suffix the naming flag chooses (§4.1), so these sensors' displayed names and composed
  IDs differ by mode, as the charger's own do.
- **Everything not a circuit entity** (panel, BESS, MID, EVSE, PV, binary sensors, adopted properties, and circuit-scoped extension entities, which stay on the
  panel's device): stock behavior, label-composed.

### 4.6 Legacy `use_device_prefix`

Stored, preserved by the options flow, and read by `get_current_naming_pattern`, but it **cannot keep the device names out of a composed ID** — Core composes
them under the user's own `entity_id_parts`. An install predating the option, whose circuit IDs carry no device half, is offered one on Recreate. That is Core
composing under the user's settings, and it is documented in the README rather than special-cased.

### 4.7 The creation preset in circuit-numbers mode

A circuit's device is named by the circuit, so Core would compose a new entity's ID from that name. Circuit-numbers mode promises an ID that carries the breaker
position instead (R3), and keeps the promise with the one `entity_id` this integration presets (R2).

- **When.** `SpanPanelEntity._circuit_number_entity_id` answers only in circuit-numbers mode and only for an entity being created: one whose unique ID has
  neither an active registry row nor a row among the registry's `deleted_entities` (`circuit_number_ids.preset_circuit_number_entity_id`). An existing entity's
  ID never moves (R5). A control the panel withdrew, or an entity the user deleted, is restored by Core with the ID it had. An entity added later to an existing
  circuit, such as the breaker switch of a circuit that becomes controllable, is being created and gets its circuit-number ID the same way.
- **What.** The ID Core would compose under the user's entity-ID format were the circuit's device named by the identifier (§4.1):
  `sensor.span_panel_circuit_15_power`, or `sensor.span_panel_circuit_30_32_power` for a two-pole breaker. The suffix is the wording a new entity gets, decided
  against the number and not the device's name, so a circuit named "Solar Power" is created as `..._circuit_15_power`. Where the ID is taken, Core's own clash
  suffix is added (`async_get_available_entity_id`).
- **How it is composed.** Core has no public function that composes an ID with another name in the device's place, so
  `circuit_object_ids.compose_circuit_object_id` mirrors Core's private composer from its public parts: the user's saved format or the default; the area of the
  circuit's device, or the panel's where that device has none or is not registered yet; that area's floor; the panel's name as the parent device, only while the
  circuit's device has no area of its own; the circuit number as the device; the suffix words as the entity. `tests/test_circuit_number_ids.py` holds the
  composer to Core's own answer for each format and area case (`test_composer_matches_core`), the formats that put the entity before the device among them, so a
  change in Core fails there on the next Home Assistant bump instead of drifting silently. The read-back of §4.3 uses the same composer.
- **A part it does not know.** The composer names the five parts Core has today (`circuit_object_ids.COMPOSED_PARTS`), and
  `test_composer_knows_every_part_core_composes_an_id_from` holds that set to Core's enum, so a part a later Home Assistant adds fails the suite at the bump. At
  run time a saved format holding any other part gets no composition: the entity is created without a preset, with the ID Core composes, which is logged at
  debug, and an existing ID is read by its tail (§4.3). No platform fails to set up over it.
- **How it is spent.** Core stores the preset as the row's `suggested_object_id` and uses it verbatim. The entity's next add supplies none, so Core clears it
  (§3.3). A new entity asks for a reload on its first update (§5.2), so that add follows its creation directly, or, for an entity created while Home Assistant
  is starting, once it has started. The ID stays where it is (R5), and from then on Recreate composes from the circuit's device and the base, and offers the
  circuit's name.
- **Where there is none.** In circuit-names mode, for a sub-device sensor (§4.5), where the user's format composes nothing for the entity, which is left to
  Core's own fallback, and where it holds a part the composer does not know.

Presetting on every load was rejected. It would keep Recreate offering nothing in this mode, and it would make R2's exception permanent (§3.4).

## 5. Name sync — what "phase 1" and "phase 2" mean, and what they mean now

The terms come from the pre-2.1.0 design and are still used in conversation, so here is what each was and what it is today.

### 5.1 Before 2.1.0 (the two-phase scheme, for context only)

- **Phase 1 — construction.** `_attr_name` was set from the _naming flag_ (`Circuit 15 Power` in circuit-numbers mode) so that Core would derive a flag-shaped
  `object_id_base`, and `entity_id` was **preset** by a builder, which Core stored as `suggested_object_id`. A new entity displayed the flag name until phase 2.
- **Phase 2 — first coordinator update, and every rename after.** Circuit-numbers mode **wrote the registry `name`** with the panel's name to fix the display.
  That put the panel name in the user's field, which outranks everything in ID generation — so Recreate proposed a circuit-name ID for every circuit-numbered
  entity, and a rename in the SPAN app could never reach the preset `suggested_object_id` (#252). Circuit-names mode's phase 2 was idempotent (same string) and
  wrote nothing.

From 2.1.0 to 2.2.x there was one path: `_attr_name` was `"{circuit name} {label}"` in both modes, every circuit entity sat on the panel's device, and the base
carried the identifier with the suffix (`Kitchen Outlets power` or `Circuit 15 power`).

### 5.2 Since 2.3.0 the circuit's name is its device's

There is one name path, in both modes:

1. **Construction** (`sensor_base.py`, `switch.py`, `select.py`): `_attr_name` is the label alone, unconditionally, existing entity or not. It reaches the
   registry as `original_name`. The entity's device info names the circuit's device with the _panel's_ current name for the circuit (§4.1). What the base (§4.3)
   is worked out from is recorded at the same time, `suggested_object_id` works the base out from it as Core adds the entity (§4.4), and in circuit-numbers mode
   an entity being created is given its preset (§4.7). If an existing row carries a `name` a pre-2.1.0 release wrote, it is released (§6.1).
2. **Every coordinator update** (`_handle_coordinator_update` / `_sync_circuit_name`): the entity compares the panel's current circuit name with the one it was
   built from.
   - A registry `name` the user set changes nothing here. Their name is the entity's own and is never written; the circuit's name is the device's, and a rename
     in the SPAN app always reaches the circuit's device. An entity that skipped the reload because it was named would leave the device on the old name, for
     good where the user has named every entity of the circuit.
   - If the entity was **not** in the registry at construction (`_previous_circuit_name is _NAME_UNSET`), the first update requests one reload. This is the
     surviving trace of "phase 2": a freshly created entity's registry row is settled by that reload, which is also what spends a creation preset.
   - If the name **changed**, the entity requests a reload (`coordinator.request_reload()`).
3. **Reload** (requested at the end of the update cycle in `coordinator.py`, started by `entry_reloads` once that update has returned, or once Home Assistant
   has started): the entry is torn down and rebuilt. The integration names a circuit's device only through the device info of the entities it adds, so a rebuild
   is the only way to refresh the name — that is why a rename costs a reload rather than a registry write. The rebuild also recomputes the base against the
   device's new name and, per §3.3, rewrites the row's `object_id_base`.

A name the user gave the circuit's _device_ in Home Assistant (`name_by_user`) outranks the panel's in every displayed name and composed ID. That is Core's
rule, not a check made here: the reload still stores the panel's new name underneath it.

### 5.3 A circuit's device renamed, or an area set or renamed, in Home Assistant

The base depends on the device's name: it is empty where the name already ends on the suffix words, and an ID is read against the name (§4.3). A rename in the
SPAN app reloads the entry, which builds every entity again. A rename of the device in Home Assistant reloads nothing, and Recreate composes from the stored row
(§3.4), so a base decided for the old name would be offered under the new one. A circuit named "Solar Power" on `sensor.span_panel_solar_power`, whose base is
empty, renamed "Roof Array" would be offered `sensor.span_panel_roof_array`, an ID no entity on that device is created with, and the ID would stay once
accepted.

So each circuit entity records what its base is worked out from as it is built, enabled or not (`SpanPanelRuntimeData.circuit_id_bases`), and
`circuit_object_ids.async_keep_circuit_id_bases_current`, registered at setup, works a circuit's bases out again when its device's `name_by_user` changes and
stores those that differ. It runs inside the device registry's update event, before the rename returns, because Home Assistant asks whether to rename the
device's entity IDs straight after it; a reload would answer too late. The base is stored by registering the entity again with it, which is how the platform's
own add refreshes a row (§3.3). No entity ID moves (R5): "Solar Power" renamed "Roof Array" is then offered `sensor.span_panel_roof_array_power`.

The base depends as well on whether the entity has an area of its own, which decides whether Core puts the device's name in front of it at all (§4.3). Setting
or clearing an entity's area reloads nothing either. The same listener therefore works that one entity's base out again inside the entity registry's update
event, when the row's `area_id` changes. The "Solar Power" sensor given the area Backyard is offered `sensor.backyard_power` at once, where the empty base would
offer `sensor.backyard`, and with the area taken away again it is offered its own ID, where a base kept for the area would offer
`sensor.span_panel_solar_power_power`.

An ID Core composes today is given the base that composes it (§4.3), so the base depends too on every other part a composition reads: the area of the circuit's
device, and the panel device's name and area, which a circuit device with no area of its own takes. None of them reloads anything when it changes. A circuit
named "Power" whose label-only ID is `sensor.garage_power` has the empty base while its device is in Garage, where Core composes that ID from the device with
the suffix left out, and `power` once the device is not. With the entity's area cleared before the device's was set, a base stored for the device with no area
offered `sensor.garage_power_power` until the next restart, for an ID that is offered nothing after it. So the same listener works a circuit's bases out again
when its device's `area_id` changes, and every circuit's when the panel device's `name_by_user` or `area_id` changes. A circuit device with no area of its own
takes the panel's under every format with an area part. Under `(AREA, DEVICE, ENTITY)`, which names no panel, the "Power" sensor's `sensor.garage_power` is
composed again from the panel's area Garage and the device with the suffix left out, so putting the panel in Garage leaves nothing to offer. Under the default
and `(PARENT_DEVICE, DEVICE, ENTITY)`, which name the panel, `sensor.span_panel_power` on a circuit named "Power" is no composition once the panel is renamed
"Main Panel", and it is offered `sensor.main_panel_power_power` at once rather than after the next restart.

A composition reads an area's name too, under a format with an area part, and the floor an area is on and that floor's name under one with a floor part.
Renaming an area or a floor reloads nothing either. With the device of a circuit named "Power" in Garage and its ID `sensor.garage_power`, renaming Garage
"Workshop" leaves an ID that no composition spells, read for its `power`. A base left as it was offered `sensor.workshop_power` until the next restart and
`sensor.workshop_power_power` after it. So the listener works every circuit's bases out again when an area is renamed or moved to another floor, or a floor is
renamed, where the user's format reads what changed (`circuit_object_ids._async_follow_area_and_floor_names`). Core announces every edit to an area or a floor,
an icon or an alias as well, without saying what changed, so the listener keeps each area's name and floor and each floor's name to compare with. It refreshes
every circuit, rather than only those composed in that area, because which areas an ID is composed in is the composer's to say, and a second statement of it
could drift.

The base reproduces the ID the entity has, so an entity ID the user edits by hand reloads nothing either and changes what the base is read from. The listener
works the entity's base out again when the row's `entity_id` changes, as it does for its `area_id`, and nothing else does. Core changes a live entity's ID in
place and writes no base (§4.4), and a disabled entity has no live entity for Core to change. So setup registers the listener before the platforms add the first
entity, and Core stops it with the entry's on-unload callbacks once the entities are gone: an unload runs them after the platforms have removed every entity,
and a setup that fails after the forward unloads its platforms before the error leaves it and Core runs them. Two cases leave live entities without the
listener, and an ID edited on one of them keeps the base from before the edit until the next restart: a platform that does not unload as a failed setup is taken
down, which is logged, and a setup cancelled part way, as when Home Assistant stops during it, which takes nothing down. Edited to
`sensor.span_panel_refrigerator`, which Core composes from the device with nothing after it, the entity is offered nothing. A base kept from when the entity was
built offered the old ID back, `sensor.span_panel_refrigerator_power`, until the next restart, which reverted the user's edit. Accepting a Recreate offer is
such an edit too, and reading the accepted ID again gives the base it was composed from, so nothing more is offered.

One change applies only after the next restart: a saved entity-ID format, for which Core announces no event. Everything else the base is worked out from either
reloads the entry when it changes, as the circuit's name in the SPAN app and any change to the entry's data, options, title or unique ID do, or is watched
above. The panel device's own name is the one the entry was set up with; the user names it through `name_by_user`.

The rename therefore reaches:

| What                      | Circuit names                                                | Circuit numbers                                                                                                                   |
| ------------------------- | ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| Circuit device's name     | new name, after the reload                                   | new name, after the reload                                                                                                        |
| Display name              | `Span Panel Kitchen Outlets Renamed Power`, after the reload | the same; the two modes display alike                                                                                             |
| Entity ID                 | unchanged (R5)                                               | unchanged (R5)                                                                                                                    |
| Row `object_id_base`      | `power` — the name is not in it                              | `power` — the name is not in it                                                                                                   |
| Recreate entity IDs offer | `sensor.span_panel_kitchen_outlets_renamed_power`            | `sensor.span_panel_kitchen_outlets_renamed_power` — the circuit's name, where the ID is `sensor.span_panel_circuit_15_power` (R3) |
| Registry `name`           | untouched                                                    | untouched                                                                                                                         |

Settability changes (a circuit becoming or ceasing to be controllable) are **not** watched here; `SpanPanelCoordinator._check_settability_change` does that,
because the "gains a control" edge happens on circuits that have no entity to see it.

## 6. What an upgrade writes to the registry

### 6.1 Releasing the registry `name` a pre-2.1.0 release wrote

`naming.release_registry_name_written_by_older_release(registry, entity_id, circuit_name, description_names)` runs once per existing circuit entity at
construction, and in the conversion (§6.2) before anything there reads a name as the user's. It clears the registry `name` **only** if it equals
`"{circuit_name} {label}"` for the description's current label _or any label it has carried before_ (`SpanPanelCircuitsSensorEntityDescription.legacy_names`:
`Energy Consumed` / `Energy Produced` / `Energy Net` beside the current `Consumed Energy` etc.). Any other value is the user's and is left exactly where it is.
The switch releases `"{name} Breaker"`, the select `"{name} {description name}"`.

This is a hand-back, not a claim. Miss a release site and that platform's Recreate stays poisoned: a stale `name` takes the label's place under the circuit's
device, so 2.0.8's `Kitchen Outlets Consumed Energy` would propose `sensor.span_panel_kitchen_outlets_kitchen_outlets_consumed_energy`.

### 6.2 The conversion to circuit devices (2.3.0)

Releases before 2.3.0 put every circuit entity on the panel's device, named `"{circuit name} {label}"`.
`circuit_device_conversion.async_convert_circuit_entities` carries an existing registry across. It runs at every setup, once the panel's data has loaded and
before the platforms are forwarded, and acts only on circuit entities still on the panel's device, so it does nothing once done. A later setup converts only the
entities it is the first to build, such as a breaker switch once controls are switched on. The config entry's step to version 8 changes nothing itself: it is
there so that Home Assistant refuses a downgrade to a release that would put the circuits back on the panel's device.

For each circuit with such an entity, and only for the entities this setup's platforms build, in this order:

1. each entity's displayed name is recorded;
2. the circuit's device is created, as the platforms create it (§4.1);
3. names an older release wrote are released (§6.1);
4. the circuit's area moves to its device, by the area rule below;
5. a user's name that begins with the circuit's name is trimmed, since the device now says it;
6. each entity is moved to the device, its `original_name` set to its label, and its area and name written, in one registry update.

No entity ID and no unique ID changes (R5). The platform add that follows gives each row its suffix-only base. A repair lists every displayed name and area that
changed (`conversion_repairs`): an entity that gave up an area of its own, and one that had none, was in the panel's area and is now in the one its circuit's
device has. A later setup that converts more adds its lines to the notice and shows it again, dismissed or not.

**The area rule** (`_area_for_the_device`). An entity with no area of its own is in its device's, and an area-wide turn-off reaches every switch in the area, so
the breaker switch decides:

1. The circuit has a breaker switch with an area of its own: the device takes that area.
2. The circuit has no breaker switch: the device takes the power sensor's area, or failing that the first area among the circuit's other entities, in the
   registry's order.
3. The circuit has a breaker switch with no area of its own: nothing moves.

Under rules 1 and 2 every other entity of the circuit gives up its own area and inherits the device's. Under rule 3 the entities that have areas of their own
keep them, so Core displays them without their devices, as just "Power", and Recreate offers them their label alone, such as `sensor.kitchen_power`. That offer
is R1's stated exception: it matches the displayed name. Setting an area on the circuit's device and clearing the entities' own restores both the displayed name
and, once Recreate's offer is accepted, the ID, with three exceptions, in whichever order the two steps are taken:

- A circuit named exactly what its entity measures, such as "Power", keeps its label-only ID where Core composes that ID today from the device's name with
  nothing after it: under `(DEVICE, ENTITY)`, under `(PARENT_DEVICE, DEVICE, ENTITY)` once the device has an area, and under the default once the device has the
  area the entity had, `sensor.kitchen_power`. An ID Core composes today is offered nothing (§4.3), so it does not take the measurement back.
- An ID that kept both halves on a circuit whose name ends on the suffix, `sensor.span_panel_solar_power_power`, comes back without the repeat,
  `sensor.span_panel_solar_power`. Its label alone, `sensor.kitchen_power`, carries nothing of the device's name, so the suffix is left out by the device's
  name, as it is for a new entity (§7).
- A circuit named "Current" whose power sensor is on the `current_power` wording, `sensor.span_panel_current_current_power`, comes back on `power`,
  `sensor.span_panel_current_power`. Its label alone, `sensor.kitchen_current_power`, reads as the device "Current" and then `power`, which is also how
  `sensor.span_panel_current_power` must read on that device, and no reading of the ID and the names alone tells the two apart (§7). Under `(DEVICE, ENTITY)`
  Core composes the label alone from the device and `power`, so nothing is offered.

A switch the integration withdrew, which Core keeps among its deleted entities, counts as the circuit's switch. A device that already has an area keeps it.

**The trim** (`naming.trim_leading_circuit_name`) is R4's second exception. "Kitchen Outlets Power usage" is stored as "Power usage" and still displays as "Span
Panel Kitchen Outlets Power usage". The rule is Core's own, the one its 2026.11 migration applied to names that began with their device's: case is ignored, a
space, hyphen or colon must follow the circuit's name, a lower-case first word is capitalized, and a name that is the circuit's name and nothing more becomes
empty. Like that migration, it skips an entity that keeps an area of its own, whose name already displays without the device. A name that does not begin with
the circuit's name, such as "Fridge Power", is left alone and displays with the circuit's name in front.

## 7. Offers that are legitimate — and the ones that are not fully avoidable

Recreate offers a different ID only where the user's own configuration composes one. The README's Entity Naming Pattern section gives users the general rule:
Recreate offers the ID Home Assistant builds now from the panel's name, the name of the circuit's device, what the entity measures and the user's own entity ID
settings. It names seven of these rows outright: a circuit renamed in the SPAN app, circuit-numbers mode, an entity with an area of its own, a saved format with
no parent-device part, a SPAN Drive feed circuit, a second panel, and an install predating the device prefix. The CHANGELOG has three lines on it: under 2.1.0,
that Recreate proposes the IDs the panel would produce now and that a panel device the user renamed is offered IDs carrying that name, and under 2.3.0 the
circuit-numbers offer. The other rows, the three uncaused ones among them, are spelled out here alone:

| Situation                                                                                                         | Offer                                                                               | Cause                                                                                                                                                          |
| ----------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Circuit renamed on the panel, either mode                                                                         | the new name's ID                                                                   | user renamed it                                                                                                                                                |
| Circuit-numbers mode, an entity on its circuit-number ID                                                          | the circuit's name's ID                                                             | the name the user gave the circuit in the SPAN app (R1, R3)                                                                                                    |
| Panel device in an area, `entity_id_parts` includes AREA (the default)                                            | area-prefixed ID                                                                    | user assigned the area                                                                                                                                         |
| Circuit device in an area of its own, including one the conversion moved there                                    | the area in front and the panel's name gone: `sensor.kitchen_kitchen_outlets_power` | user assigned the area                                                                                                                                         |
| Entity with an area of its own, including the conversion's rule-3 circuits (§6.2)                                 | its label alone: `sensor.kitchen_power`                                             | user assigned the area; R1's stated exception                                                                                                                  |
| That entity's area cleared, where its label alone cannot carry what its ID had (§6.2)                             | the ID a new entity there gets: `sensor.span_panel_solar_power`                     | the area move is the user's; the half or wording lost is lost because the label alone cannot carry it. Caused, by the maintainer's ruling of 2026-11-15        |
| Panel device renamed by the user (`name_by_user`)                                                                 | ID carrying that name                                                               | user renamed the device                                                                                                                                        |
| Circuit device renamed by the user (`name_by_user`)                                                               | ID carrying that name                                                               | user renamed the device                                                                                                                                        |
| Entity renamed by the user (registry `name`)                                                                      | ID built from their name, with the circuit's device in front                        | user renamed the entity                                                                                                                                        |
| A saved `entity_id_parts` with no parent-device part                                                              | ID without the panel's name: `sensor.kitchen_outlets_power`                         | the user's format                                                                                                                                              |
| Sensor on a SPAN Drive feed circuit                                                                               | charger-named ID                                                                    | device part is Core's                                                                                                                                          |
| Second panel whose generated device name is "Span Panel 2"                                                        | `span_panel_2_…`                                                                    | device part is Core's                                                                                                                                          |
| Install predating `use_device_prefix`, circuit IDs with no device half                                            | device-prefixed ID                                                                  | device part is Core's                                                                                                                                          |
| Unnamed circuit in circuit-names mode whose ID carries `single_circuit`/`power_2`                                 | `circuit_7…`                                                                        | **uncaused** — no computed identifier reproduces both eras; accepted and documented                                                                            |
| Unnamed charger circuit whose sensors are not on the charger's device, in circuit-names mode, on `ev_charger` IDs | `circuit_7…`                                                                        | **uncaused** — the circuit's device is named by its tabs (noted below); accepted and documented, by the maintainer's ruling of 2026-11-15                      |
| Inverter card a remote CT feeds, on a panel with several inverters, either mode                                   | `…_remote_ct_3_{pv_panel_link, pv_nameplate_capacity, pv_product, pv_vendor}`       | **uncaused** — the card's name was built from the remote CT's wire id, which no name may be; accepted and documented, by the maintainer's ruling of 2026-11-20 |

The charger row's circuit is an unnamed circuit that feeds a charger without being the one the charger's device takes its sensors from: the second circuit of a
charger fed by two. A charger's device holds the sensors of one feed circuit only, so this circuit's sensors are on its own device. They were named "EV Charger"
while its switch and select were named by its tabs, and its device takes the tabs (§4.1). Besides the offer, its never-renamed sensors change displayed name, in
both modes: `Span Panel EV Charger Power` becomes `Span Panel Circuit 7 Power`, which the conversion's repair lists. Naming the device "EV Charger" instead
would move the same two changes to the circuit's switch and select, so the device keeps its tabs and the offer is accepted as the row above it is.

The last row's card is an inverter's own, on a panel with more than one, named after the circuit that feeds it. A remote CT has no breaker positions, so the
positions fallback named it by the remote CT's wire id: `Span Panel Solar Inverter (Circuit remote-ct-3)`, and after r202645 the remote CT's full device id in
place of `remote-ct-3`. It is now `Span Panel Solar Inverter (Remote CT 3)`, the label the remote CT's own device and entities have always carried, in both
modes. The card's four entities keep their IDs, and their displayed names follow the card's: `Span Panel Solar Inverter (Remote CT 3) PV Panel Link`,
`… PV Nameplate Capacity`, `… PV Product` and `… PV Vendor`. Recreate offers each the ID composed from the new name. A new install creates those IDs, so it is
offered nothing.

Anything not in this table that Recreate offers on an unchanged install is a bug against R1.

## 8. Things that must stay true (checklist for any change touching naming)

- [ ] `self.entity_id` is assigned in three places and no others: `sensor_base.py`, `switch.py` and `select.py`, each with what
      `SpanPanelEntity._circuit_number_entity_id` answers, which is `None` outside circuit-numbers mode and for any entity that is not being created. Nothing
      assigns `_attr_entity_id`. `tests/test_docs_agree_with_code.py` holds this item to the code.
- [ ] The registry `name` is assigned a value in one place, the conversion's trim (§6.2); the only other `async_update_entity(..., name=...)` call is the `None`
      in `release_registry_name_written_by_older_release`.
- [ ] `_attr_name` is the label alone in both modes; nothing puts the circuit's name in it, and nothing branches on `use_circuit_numbers` to name a circuit's
      device or its entities. Every function the flag is passed to is named in §4.1, which `tests/test_docs_agree_with_code.py` holds to the code.
- [ ] A circuit's device is named by `helpers.circuit_device_name` wherever it is registered, in both modes.
- [ ] The base is the suffix alone, computed from `(id_identifier, device_name, canonical suffix, existing_entity_id)` and what Core composes under the user's
      entity-ID format, and nothing else — never from the display label, and never carrying the circuit's name.
- [ ] Nothing reads an ID as though the format ended with the device and then the entity without being told it does (`device_then_entity`), and an ID Core
      composes today is offered nothing under any format (§4.3).
- [ ] What a circuit entity's base is worked out from is recorded when the entity is built. The base is worked out as Core adds the entity, and again when its
      device is renamed in Home Assistant, when its device's area, the panel device's name or the panel device's area changes, when an area or a floor the
      format reads is renamed or an area moved to another floor, or when its own area is set or cleared or its ID edited (§5.3). A saved entity-ID format is the
      one change that reaches the base only at the next restart. An entity with an area of its own never has an empty base (§4.3).
- [ ] The composer answers nothing for an entity-ID format holding a part outside `COMPOSED_PARTS`, and nothing indexes a table of parts by a part it may not
      hold (§4.7).
- [ ] `ENTITY_ID_SUFFIX_FORMS` gains an entry only for a form found to have shipped; `NEW_ENTITY_ID_SUFFIX_WORDS` and it stay in one-to-one correspondence
      (`test_every_form_table_entry_has_a_new_wording_and_vice_versa`).
- [ ] A reworded description label adds the old label to `legacy_names` so §6.1 still recognizes what 2.0.8 wrote.
- [ ] Unique IDs and circuit device identifiers are untouched by any of this; `CIRCUIT_SUFFIX_MAPPING` is closed. `device_id_rekey` re-keys both once,
      deliberately, when firmware gives a panel's circuits new device ids. It keeps every `entity_id` and device registry id, so R5 holds. It keeps every name
      and base too, except one built from the raw id by the `Circuit {circuit id}` fallback of `construct_circuit_identifier_from_tabs`, for a circuit with no
      breaker positions to name it by, which then names the new id; R1 holds wherever no name is built from the id.
- [ ] `tests/test_naming.py`, `tests/test_recreate_entity_ids.py` and `tests/test_circuit_number_ids.py` pass. The last two drive a real `EntityPlatform` and
      the real registries, so a composition regression fails there rather than in a unit test of a builder.
- [ ] Run the `entity-id-auditor` agent after editing `id_builder.py`, `entity_resolver.py`, `naming.py`, `circuit_object_ids.py`, `circuit_number_ids.py` or
      any circuit platform file.

## 9. Diagnosing "Recreate offered nothing" on a live install

In circuit-numbers mode an offer of the circuit's name is expected (R3), and after it is accepted nothing more is offered. For an offer that is missing:

1. Confirm the running code: `manifest.json` version and the presence of `custom_components/span_panel/naming.py`. A 2.0.8 install with a newer release selected
   in the Marketplace but not yet loaded shows exactly this symptom, because its rows still hold the 2.0.8 preset in `suggested_object_id` (§3.4).
2. Read the entity's row in `.storage/core.entity_registry`: on 2.3.0 after at least one reload, `suggested_object_id` is `null` and `object_id_base` reads the
   suffix (`breaker`), or is empty where the ID stops at the circuit's name. A non-null `suggested_object_id` means the entity has not been added again since
   the preset was made.
3. Check `name` on the row: a non-null value is a user override (or an unreleased 2.0.8 write — compare against §6.1's patterns) and outranks the base.
4. Read the row's device in `.storage/core.device_registry`: it is the circuit's, a child of the panel's, and its `name` is the panel's current name for the
   circuit. A `name_by_user` there outranks it. A row still on the panel's device has not been converted (§6.2).
5. Confirm the rename-triggered reload happened: the log carries `Auto-sync detected circuit name change from '…' to '…'` followed by the entry reload.
6. Only then look at `circuit_object_id_suffix` with the row's exact `entity_id`, the identifier, the device's name and the suffix — `tests/test_naming.py`
   shows how to call it directly. It is reached only for an ID no wording composes under the saved entity-ID format (§4.3), so check that format first.

## 10. Where the history lives

The design trail (issue #252 write-up, the 2026-08-22 spec and plan, the 2026-08-26 composition-route review, the 2026-11-14 circuit-device design and its
decisions) lives in the `SpanPanel_Docs` workspace, not in this repository. It records how the rules were arrived at and the routes that were rejected. It is
history; this document is the rule.
