"""Identity of circuit entities: the base Home Assistant composes an id from.

Since Home Assistant 2026.8 the user decides how an entity id is composed
(`entity_id_parts`: area, parent device, device, entity). This integration
supplies one *base* per circuit entity and Core assembles the rest from the
user's settings. `SpanPanelEntity.suggested_object_id` hands the base to Core.
The one `entity_id` it presets is a circuit entity's in circuit-numbers mode,
once, when the entity is created, and that id is composed under the same
settings (`circuit_number_ids`).

Each circuit's entities live on a device of the circuit's own, named by the
circuit and a child of the panel's, so Core composes the panel's name and the
circuit's from the two devices. The base is the suffix alone -- `power`,
`energy consumed` -- or empty where the existing id stops at the circuit's
name (`circuit_object_id_suffix`).

The base must reproduce an existing entity's id *where Home Assistant composes
it under the entity-id options that match how that install was built*: it
carries the install's own suffix wording, read back from the id the entity
already has, because this integration has shipped two spellings
(`consumed_energy` before it preset ids, `energy_consumed` after). It never
bypasses `entity_id_parts` with a hard-coded id -- where composition yields a
different device part, that is the user's configuration at work and the offer
is legitimate. A new entity gets the noun-last wording, which is what the
panel-level ids (`main_meter_consumed_energy`) and the display labels use.

The base is *not* the display name. The two are decoupled on purpose, so a
label can be reworded without touching a single id.

`CIRCUIT_SUFFIX_MAPPING` in `id_builder` builds unique ids and is closed; the
tables here govern entity ids only.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class _NameReading:
    """One reading of an id's tail as a name: `<name>_<form>`, or `<name>` alone."""

    tail_length: int
    """How many characters at the end of the id the reading accounts for."""

    form: str | None
    """The suffix form spelled after the name, or None where the id stops at it."""


def _read_after_the_name(object_id: str, name: str, suffix: str) -> _NameReading | None:
    """Read this id's tail as `name` then a form, or as `name` alone; None if it is neither."""
    name_slug = slugify(name)
    form = _form_after_the_identifier(object_id, name_slug, suffix)
    if form is not None:
        return _NameReading(len(f"{name_slug}_{form}"), form)
    if _ends_on_segment(object_id, name_slug):
        return _NameReading(len(name_slug), None)
    return None


def circuit_object_id_suffix(
    id_identifier: str, device_name: str, suffix: str, existing_entity_id: str | None
) -> str:
    """Return the base for one circuit entity on its circuit's device: the suffix alone.

    Home Assistant composes the circuit's name from the device, so the base
    carries only the suffix words (`power`, `energy consumed`). It is empty --
    Core then composes the id from the other parts of the user's entity-id
    format, the devices under the default one -- in two cases: the existing id
    is read (below) as stopping at the device's name, or, where the id does not
    name the device or there is no id, the device name already ends with the
    words.

    `id_identifier` is the identifier the naming flag resolves today: the tabs
    in circuit-numbers mode (`Circuit 15`), the circuit's current name in names
    mode (`Kitchen Outlets`). `device_name` is the name Core composes the id
    from: the circuit device's, or the user's rename of it where there is one.
    `suffix` is this entity's canonical suffix, which the sensors derive from
    their description key via `id_builder.get_user_friendly_suffix` while the
    switch and the select -- which have no such key -- name theirs outright
    ("breaker", "circuit_priority").

    An existing id is read against two names, the device's and the identifier.
    Each reading says what follows the name: a known form of the suffix, or
    nothing, where the id stops at the name.

    Where the id names the device, that reading settles two things at once --
    which form the id carries, and whether it left the form out. They are one
    question: an id reading `..._solar_power_power` carries `power` and omitted
    nothing, while `..._solar_power` omitted it. Answering each on its own --
    the form by a plain `endswith`, the omission from the name alone --
    disagreed with the id in both directions. A circuit named "Current" had
    `..._current_power` read as the `current_power` form and was offered
    `..._current_current_power`; a circuit named "Solar Power" whose id kept
    both halves was offered `..._solar_power`. Both are renames the user did
    not cause, which R1 forbids.

    The device's name is read because an id Core composes from the circuit
    device carries it wherever the user's entity-id format includes the
    device, as the default does: an offer the user accepts then reads back to
    the base it was composed from, so the restart after it offers nothing more.
    Read against the identifier alone, a numbers-mode circuit named "Current"
    accepted `..._current_power`, which then named neither, and the next
    restart offered `..._current_current_power`.

    The identifier covers ids built from it rather than from the device -- the
    circuit-numbers preset `..._circuit_15_power`, and a names-mode id whose
    device the user has since renamed. Such an id settles the form only: what
    it spells after the identifier, or failing that at its end. Whether the
    suffix is left out is a question about the name in front of it, and the
    offer is composed from the device's name, not the identifier, so it is
    decided from `device_name`, as it is for a new entity. That is what makes
    the offer in circuit-numbers mode the id the circuit has under circuit
    names: a circuit named "Solar Power" on `..._circuit_15_power` is offered
    `..._solar_power`, and a breaker named "Main Breaker" `..._main_breaker`,
    where reading the omission from the id as well offered
    `..._solar_power_power`. Once accepted, the id names the device and reads
    back to the same base.

    Where both names read the id, the reading that accounts for more of its
    tail wins, because the suffix word can itself be a device's name. The
    trailing `power` of `..._circuit_15_power` also reads as an id that stops
    at a device named "Power"; the tabs account for `circuit_15_power`, the
    device for `power` alone, so the id is the tabs' and its form is `power`,
    which the leading-underscore rule below then keeps on that device. Where
    the two account for the same tail they are the same characters read two
    ways -- `..._solar_power` as device "Solar Power" stopping, or as circuit
    "Solar" then `power` -- and the device wins, because its reading reproduces
    the id from the name Core composes it from now.

    Where the id names neither -- the circuit was renamed on the panel, issue
    #252 -- the form is read back by itself, from the end of the id, and a new
    entity gets the noun-last wording. In both the omission is decided from
    `device_name` too. That is the whole point of the rename: the name half
    follows the panel and the suffix half does not.

    The omission test carries a leading underscore because the builder's did: a
    circuit named exactly "Power" is not a repetition of anything and kept both
    halves, `..._power_power`.
    """
    form: str | None = None
    if existing_entity_id is not None:
        object_id = existing_entity_id.split(".", 1)[-1]
        by_device = _read_after_the_name(object_id, device_name, suffix)
        by_identifier = _read_after_the_name(object_id, id_identifier, suffix)
        # `>=`, so the device wins a tie.
        if by_device is not None and (
            by_identifier is None or by_device.tail_length >= by_identifier.tail_length
        ):
            return by_device.form.replace("_", " ") if by_device.form else ""
        if by_identifier is not None:
            form = by_identifier.form

    if form is None:
        form = _existing_suffix_form(existing_entity_id, suffix)
    words = (
        form.replace("_", " ")
        if form
        else NEW_ENTITY_ID_SUFFIX_WORDS.get(suffix, suffix.replace("_", " "))
    )
    if slugify(device_name).endswith(f"_{slugify(words)}"):
        return ""
    return words


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
