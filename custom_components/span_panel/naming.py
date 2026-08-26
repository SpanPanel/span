"""Identity of circuit entities: the base Home Assistant composes an id from.

Since Home Assistant 2026.8 the user decides how an entity id is composed
(`entity_id_parts`: area, device, entity). This integration no longer presets
an `entity_id`; it supplies one *base* per circuit entity -- `Circuit 15 power`
or `Kitchen Outlets power` -- and Core assembles the rest from the user's
settings. `SpanPanelEntity.suggested_object_id` hands the base to Core.

Two rules bound what the base may be:

- An existing entity must be proposed its own id when nothing changed. Its
  suffix wording is therefore read back from the id it already has, because
  this integration has shipped two spellings (`consumed_energy` before it
  preset ids, `energy_consumed` after). Recreate must never offer a rename
  the user did not cause.
- A new entity gets the noun-last wording, which is what the panel-level ids
  (`main_meter_consumed_energy`) and the display labels use.

The base is *not* the display name. The two are decoupled on purpose, so a
label can be reworded without touching a single id.

`CIRCUIT_SUFFIX_MAPPING` in `id_builder` builds unique ids and is closed; the
tables here govern entity ids only.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import slugify

from .const import DOMAIN

ENTITY_ID_SUFFIX_FORMS: dict[str, frozenset[str]] = {
    "power": frozenset({"power", "current_power"}),
    "energy_produced": frozenset({"produced_energy", "energy_produced"}),
    "energy_consumed": frozenset({"consumed_energy", "energy_consumed"}),
    "energy_net": frozenset({"net_energy", "energy_net"}),
    "current": frozenset({"current"}),
    "breaker_rating": frozenset({"breaker_rating"}),
    "breaker": frozenset({"breaker"}),
    "circuit_priority": frozenset({"circuit_priority"}),
}
"""Every entity-id suffix form ever shipped, keyed by the canonical suffix.

Historical fact: an entry is added only when another form is discovered to
have shipped, never to introduce one.
"""

NEW_ENTITY_ID_SUFFIX_WORDS: dict[str, str] = {
    "power": "power",
    "energy_produced": "produced energy",
    "energy_consumed": "consumed energy",
    "energy_net": "net energy",
    "current": "current",
    "breaker_rating": "breaker rating",
    "breaker": "breaker",
    "circuit_priority": "circuit priority",
}
"""The wording a new entity's base ends with -- noun-last, like the panel level."""


def _known_forms(suffix: str) -> list[str]:
    """Return every form of `suffix`, longest first so `current_power` outranks `power`."""
    return sorted(ENTITY_ID_SUFFIX_FORMS.get(suffix, ()), key=len, reverse=True)


def _ends_on_segment(object_id: str, segment: str) -> bool:
    """Report whether `object_id` ends on `segment` at a word boundary.

    The equality arm is the install with no device prefix, whose object id *is*
    the circuit half and so has no underscore in front of it.
    """
    return object_id == segment or object_id.endswith(f"_{segment}")


def _existing_suffix_form(existing_entity_id: str | None, suffix: str) -> str | None:
    """Return the suffix form an existing id carries, or None if it carries none we know."""
    if existing_entity_id is None:
        return None
    object_id = existing_entity_id.split(".", 1)[-1]
    for form in _known_forms(suffix):
        if object_id.endswith(f"_{form}"):
            return form
    return None


def _form_after_the_identifier(object_id: str, identifier_slug: str, suffix: str) -> str | None:
    """Return the form this id spells *after* naming this circuit, if it spells one."""
    for form in _known_forms(suffix):
        if _ends_on_segment(object_id, f"{identifier_slug}_{form}"):
            return form
    return None


def circuit_object_id_base(identifier: str, suffix: str, existing_entity_id: str | None) -> str:
    """Return the base for one circuit entity.

    `identifier` is the naming-flag half (`Circuit 15`, `Kitchen Outlets`,
    `Unmapped Tab 32`); `suffix` is this entity's canonical suffix, which the
    sensors derive from their description key via
    `id_builder.get_user_friendly_suffix` while the switch and the select --
    which have no such key -- name theirs outright ("breaker",
    "circuit_priority").

    Where the existing id still names this circuit, what follows the name
    settles both halves at once -- which form the id carries, and whether the
    preset builder omitted it. They are one question: an id reading
    `..._solar_power_power` carries `power` and omitted nothing, while
    `..._solar_power` omitted it. Answering each half on its own -- the form by
    a plain `endswith`, the omission from the identifier alone -- disagreed with
    the id in both directions. A circuit named "Current" had `..._current_power`
    read as the `current_power` form and was offered
    `..._current_current_power`; a circuit named "Solar Power" whose id kept
    both halves was offered `..._solar_power`. Both are renames the user did not
    cause, which R1 forbids.

    Only where the id no longer names this circuit -- it was renamed on the
    panel, issue #252 -- is the form read back by itself, from the end of the
    id, and the omission decided from the *new* name. That is the whole point of
    the rename: the name half follows the panel and the suffix half does not.

    The omission test carries a leading underscore because the builder's did: a
    circuit named exactly "Power" is not a repetition of anything and kept both
    halves, `..._power_power`.
    """
    identifier_slug = slugify(identifier)
    if existing_entity_id is not None:
        object_id = existing_entity_id.split(".", 1)[-1]
        form = _form_after_the_identifier(object_id, identifier_slug, suffix)
        if form is not None:
            return f"{identifier} {form.replace('_', ' ')}"
        if _ends_on_segment(object_id, identifier_slug):
            return identifier

    form = _existing_suffix_form(existing_entity_id, suffix)
    words = (
        form.replace("_", " ")
        if form
        else NEW_ENTITY_ID_SUFFIX_WORDS.get(suffix, suffix.replace("_", " "))
    )
    if identifier_slug.endswith(f"_{slugify(words)}"):
        return identifier
    return f"{identifier} {words}"


LEGACY_PRESET_DEVICE_NAME: Final = "Span Panel"
"""The device name the preset builder spelled every circuit id with.

Not the panel's own name. The builder was called without one, so
`snapshot_to_device_info` fell back to this literal on every install -- the
second panel on a system is named "Span Panel 2" and its circuit entity ids
still began `span_panel_`. An id being *kept* has to be spelled the way it
actually is, so this is the string to keep spelling it with.
"""


def generated_panel_device_name(
    hass: HomeAssistant, entry_id: str, serial_number: str
) -> str | None:
    """Return the panel device's registered name, where this integration generated it.

    Nobody types "Span Panel 2": the config flow invents it for the second panel
    on a system (`config_flow.get_unique_device_name`) and stores it as the
    entry's `device_name`, which is what reaches the device registry. So a name
    in `name` is ours and a name in `name_by_user` is the user's, and only the
    first is a reason to keep an id from moving -- Home Assistant composes the
    device half from `name_by_user or name`, so following a rename the user made
    is following the user.

    `None` where the device is not registered yet, which is the first setup of a
    panel: entities are built before the platform creates it, and an install
    with no device has no existing id to protect either.

    Scoped to the config entry rather than searched across every one, because
    identifiers are unique only within an entry -- and a system with two panels
    is exactly the case this answers for.
    """
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, serial_number), entry_id)
    if device is None or device.name_by_user is not None:
        return None
    return device.name


def legacy_preset_entity_id(
    platform: str,
    device_slug: str | None,
    identifier: str,
    suffix: str,
    existing_entity_id: str,
) -> str:
    """Return the id an existing entity keeps, where composing one would move it.

    Three shapes Home Assistant's composition cannot reproduce, for reasons that
    belong to this integration rather than to the user:

    - a circuit sensor shown on a **sub-device** card, whose id names the panel
      because the preset builder always did, where composition names the charger
      -- that being the entity's device;
    - any circuit entity on an install with **`USE_DEVICE_PREFIX` off**, whose id
      carries no device at all, where `has_entity_name` prefixes one regardless;
    - any circuit entity on a panel whose device name **this integration
      generated** as something other than "Span Panel" -- the second panel on a
      system -- whose id says `span_panel_` all the same, where composition says
      `span_panel_2_`.

    R1 forbids offering any of those moves, so those entities go on being preset.
    Only those: `device_slug` is `None` for the prefix-less case and everything
    else composes, which is the whole point of the release.

    The id is *computed* from current panel data and never read back from the
    registry, so a circuit renamed in the SPAN app still refreshes the name half
    -- issue #252, which the preset must not undo. Only the suffix half is read
    back, by `circuit_object_id_base`, since this integration has shipped two
    spellings of it.
    """
    object_id = slugify(circuit_object_id_base(identifier, suffix, existing_entity_id))
    if device_slug:
        object_id = f"{slugify(device_slug)}_{object_id}"
    return f"{platform}.{object_id}"


def _names_another_device(device_name: str | None) -> bool:
    """Report whether composition would spell the device half some other way.

    Only a name this integration generated reaches here, so a name that is not
    "Span Panel" is one no user chose -- and every existing circuit id on that
    panel says `span_panel_` regardless, because the preset builder was never
    told the panel's name. `None` is the panel whose device is not registered
    yet, or one the user renamed, and neither is ours to hold still.
    """
    if device_name is None:
        return False
    return slugify(device_name) != slugify(LEGACY_PRESET_DEVICE_NAME)


def legacy_preset_for_existing(
    platform: str,
    *,
    identifier: str,
    suffix: str,
    existing_entity_id: str | None,
    use_device_prefix: bool,
    is_sub_device: bool,
    device_name: str | None,
) -> str | None:
    """Return the id this entity keeps, or None to let Home Assistant compose one.

    The whole R1 exception in one place, asked by all three circuit platforms --
    the sensors, the breaker switch and the priority select. Each used to preset
    its id through the same builder, so each faces the same shapes composition
    spells differently; a copy of this decision per platform is how three
    platforms drift apart on which entities the release moves.

    `device_name` is what `generated_panel_device_name` answers: the panel
    device's name where this integration generated it, and `None` where the user
    renamed it or it is not registered yet. A generated name that is not "Span
    Panel" is the third shape -- the second panel on a system, called "Span
    Panel 2" by the config flow, whose circuit ids nonetheless all say
    `span_panel_` because the preset builder was never told the panel's name.
    Composition would offer that whole panel `sensor.span_panel_2_...`, a move
    nobody asked for.

    `None` for anything new: an entity with no id yet has none to protect, and
    composing one is the point of the release. `None` too for the ordinary
    existing entity, whose id composition reproduces exactly. What is left is
    `legacy_preset_entity_id`'s shapes -- see it for why each is kept and for
    why the kept id is computed from current panel data rather than read back.
    """
    if existing_entity_id is None:
        return None
    if not (is_sub_device or not use_device_prefix or _names_another_device(device_name)):
        return None
    return legacy_preset_entity_id(
        platform,
        LEGACY_PRESET_DEVICE_NAME if use_device_prefix else None,
        identifier,
        suffix,
        existing_entity_id,
    )


def release_registry_name_written_by_older_release(
    registry: er.EntityRegistry,
    entity_id: str,
    circuit_name: str,
    description_names: Iterable[str],
) -> None:
    """Hand the registry's `name` back to the user where 2.0.8 took it.

    Circuit-numbers mode used to deliver the panel's name by writing the
    registry's `name` -- the *user's* field, which Home Assistant reads ahead
    of everything else when generating an entity id, so occupying it made
    "Recreate entity IDs" propose a friendly-name id for a circuit-numbered
    entity. Only a name this integration would have written is cleared:
    `"{circuit_name} {description name}"` for the description's current name
    and every name it has carried before. Anything else is the user's.
    """
    entry = registry.async_get(entity_id)
    if entry is None or entry.name is None or not circuit_name:
        return
    ours = {f"{circuit_name} {description_name}" for description_name in description_names}
    if entry.name in ours:
        registry.async_update_entity(entity_id, name=None)
