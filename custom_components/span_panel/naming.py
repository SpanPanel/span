"""Identity of circuit entities: the base Home Assistant composes an id from.

Since Home Assistant 2026.8 the user decides how an entity id is composed
(`entity_id_parts`: area, device, entity). This integration no longer presets
an `entity_id`; it supplies one *base* per circuit entity -- `Circuit 15 power`
or `Kitchen Outlets power` -- and Core assembles the rest from the user's
settings. `SpanPanelEntity.suggested_object_id` hands the base to Core.

The base must reproduce an existing entity's id *where Home Assistant composes
it under the entity-id options that match how that install was built*: it
carries the circuit's own name and the install's own suffix wording, read back
from the id the entity already has, because this integration has shipped two
spellings (`consumed_energy` before it preset ids, `energy_consumed` after). It
never bypasses `entity_id_parts` with a hard-coded id -- where composition
yields a different device part, that is the user's configuration at work and
the offer is legitimate. A new entity gets the noun-last wording, which is what
the panel-level ids (`main_meter_consumed_energy`) and the display labels use.

The base is *not* the display name. The two are decoupled on purpose, so a
label can be reworded without touching a single id.

`CIRCUIT_SUFFIX_MAPPING` in `id_builder` builds unique ids and is closed; the
tables here govern entity ids only.
"""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

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
