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

On a device named by the circuit, Core composes the circuit's name from the
device, so the base is the suffix alone -- `power`, `energy consumed` -- read
back the same way (`circuit_object_id_suffix`).

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


def _words_after_the_name(object_id: str, name: str, suffix: str) -> str | None:
    """Return the suffix words this id spells after `name`, "" if it stops there, else None.

    None means the id does not name `name` at all, so it answers nothing.
    """
    name_slug = slugify(name)
    form = _form_after_the_identifier(object_id, name_slug, suffix)
    if form is not None:
        return form.replace("_", " ")
    if _ends_on_segment(object_id, name_slug):
        return ""
    return None


def circuit_object_id_suffix(
    id_identifier: str, device_name: str, suffix: str, existing_entity_id: str | None
) -> str:
    """Return the base for one circuit entity on its circuit's device: the suffix alone.

    Home Assistant composes the circuit's name from the device, so the base
    carries only the suffix words (`power`, `energy consumed`). It is empty --
    Core then composes the id from the devices alone -- in two cases: the
    existing id stops at the name it is read against (below), or, where the id
    names neither name or there is none, the device name already ends with the
    words.

    `id_identifier` is the identifier the naming flag resolves today: the tabs
    in circuit-numbers mode (`Circuit 15`), the circuit's current name in names
    mode (`Kitchen Outlets`). `device_name` is the name Core composes the id
    from: the circuit device's, or the user's rename of it where there is one.
    `suffix` is this entity's canonical suffix, which the sensors derive from
    their description key via `id_builder.get_user_friendly_suffix` while the
    switch and the select -- which have no such key -- name theirs outright
    ("breaker", "circuit_priority").

    An existing id is read against two names, the device's first and then the
    identifier. Where it names either, what follows that name settles two
    things at once -- which form the id carries, and whether it left the form
    out. They are one question: an id reading `..._solar_power_power` carries
    `power` and omitted nothing, while `..._solar_power` omitted it. Answering
    each on its own -- the form by a plain `endswith`, the omission from the
    name alone -- disagreed with the id in both directions. A circuit named
    "Current" had `..._current_power` read as the `current_power` form and was
    offered `..._current_current_power`; a circuit named "Solar Power" whose id
    kept both halves was offered `..._solar_power`. Both are renames the user
    did not cause, which R1 forbids.

    The device's name comes first because every id Core composes now carries
    it: an offer the user accepts reads back to the base it was composed from,
    so the restart after it offers nothing more. The identifier covers ids
    built from it rather than from the device -- the circuit-numbers preset
    `..._circuit_15_power`, and a names-mode id whose device the user has since
    renamed. Read against the identifier alone, a numbers-mode circuit named
    "Current" accepted `..._current_power`, which then named neither, and the
    next restart offered `..._current_current_power`.

    Only where the id names neither -- the circuit was renamed on the panel,
    issue #252 -- is the form read back by itself, from the end of the id.
    There, and for a new entity, which gets the noun-last wording, the omission
    is decided from `device_name`. That is the whole point of the rename: the
    name half follows the panel and the suffix half does not.

    The omission test carries a leading underscore because the builder's did: a
    circuit named exactly "Power" is not a repetition of anything and kept both
    halves, `..._power_power`.
    """
    if existing_entity_id is not None:
        object_id = existing_entity_id.split(".", 1)[-1]
        for name in (device_name, id_identifier):
            words_after_the_name = _words_after_the_name(object_id, name, suffix)
            if words_after_the_name is not None:
                return words_after_the_name

    form = _existing_suffix_form(existing_entity_id, suffix)
    words = (
        form.replace("_", " ")
        if form
        else NEW_ENTITY_ID_SUFFIX_WORDS.get(suffix, suffix.replace("_", " "))
    )
    if slugify(device_name).endswith(f"_{slugify(words)}"):
        return ""
    return words


def circuit_object_id_base(identifier: str, suffix: str, existing_entity_id: str | None) -> str:
    """Return the base for one circuit entity on the panel's device: its identifier, then its suffix.

    `identifier` is the naming-flag half (`Circuit 15`, `Kitchen Outlets`).
    With the panel as the entity's device, the identifier is the name the id
    is composed from, so it also decides the omission. The suffix half is
    `circuit_object_id_suffix`'s answer; its docstring explains the read-back.
    """
    words = circuit_object_id_suffix(identifier, identifier, suffix, existing_entity_id)
    return f"{identifier} {words}" if words else identifier


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
