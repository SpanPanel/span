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
name (`circuit_object_id_suffix`). An id that is the entity's label alone,
which Core composed in an area of the entity's own with no device in front,
does not stop there, even on a circuit named "Power", except where the
circuit's name leaves the suffix out.

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

from collections.abc import Callable, Iterable, Sequence
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
    """Return every form of `suffix`, longest first so `current_power` outranks `power`.

    Forms of one length come in alphabetical order, so two that an id could
    both be read as are tried in the same order on every run.
    """
    return sorted(ENTITY_ID_SUFFIX_FORMS.get(suffix, ()), key=lambda form: (-len(form), form))


def suffix_wordings(suffix: str) -> tuple[str, ...]:
    """Return every wording a base has carried for `suffix`: each form ever shipped, as words.

    The wording a new entity gets is one of them. A suffix the table does not
    know has only its own words.
    """
    forms = _known_forms(suffix) or [suffix]
    return tuple(form.replace("_", " ") for form in forms)


def without_core_number(object_id: str) -> str | None:
    """Return an object id without the number Core appended to it, or None where it ends on none.

    Core composes an id and, where another entity has it, appends `_2`, `_3`
    and on until one is free: two circuits named "Lights" are
    `..._lights_power` and `..._lights_power_2`.
    """
    head, _, number = object_id.rpartition("_")
    if head and number.isdecimal() and int(number) > 1:
        return head
    return None


def _ends_on_segment(object_id: str, segment: str) -> bool:
    """Report whether `object_id` ends on `segment` at a word boundary: an underscore, or its start.

    The equality arm is an object id that is the segment and nothing more, with
    no underscore in front of it: the install with no device prefix, whose id
    *is* the circuit half, and the label-only id an entity with an area of its
    own accepts under a format with no area part, which is the suffix alone.
    """
    return object_id == segment or object_id.endswith(f"_{segment}")


def _existing_suffix_form(object_id: str, suffix: str) -> str | None:
    """Return the suffix form an existing id ends on, or None if it ends on none we know.

    It ends on one at a word boundary (`_ends_on_segment`), so an id that is the
    form alone, `energy_consumed`, ends on it. Read after an underscore only,
    such an id ended on no form, and `current_power` on the `power` it ends with.
    """
    for form in _known_forms(suffix):
        if _ends_on_segment(object_id, form):
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


def _read_after_the_name(
    object_id: str, name: str, suffix: str, *, may_stop_at_the_name: bool
) -> _NameReading | None:
    """Read this id's tail as `name` then a form, or as `name` alone; None if it is neither.

    An id that ends on the name is read as stopping there, with the form left
    out, only where `may_stop_at_the_name`: where ids are composed with the
    entity's part after the device's. Composed in another order, an id ends on
    the device's name whatever it spells for the entity.
    """
    name_slug = slugify(name)
    form = _form_after_the_identifier(object_id, name_slug, suffix)
    if form is not None:
        return _NameReading(len(f"{name_slug}_{form}"), form)
    if may_stop_at_the_name and _ends_on_segment(object_id, name_slug):
        return _NameReading(len(name_slug), None)
    return None


def _without_the_first_run(words: Sequence[str], run: Sequence[str]) -> list[Sequence[str]] | None:
    """Return the words on either side of the first place `run` occurs whole, or None if nowhere."""
    for start in range(len(words) - len(run) + 1):
        if words[start : start + len(run)] == run:
            return [words[:start], words[start + len(run) :]]
    return None


def _form_spelled_apart_from_the_name(
    object_id: str, names: Iterable[str], suffix: str
) -> str | None:
    """Return the form an id spells as whole words anywhere outside the name it carries.

    For an id composed with the entity's part somewhere other than its end,
    `power_kitchen_outlets`. The first of `names` the id spells is set aside
    before the search, so a word of the name is not read as the suffix: the id
    of a device named "Power Strip" spells `power` once for the entity and once
    for the device. Where the id spells none of them, all of it is searched.
    """
    words = object_id.split("_")
    pieces: list[Sequence[str]] = [words]
    for name in names:
        apart = _without_the_first_run(words, slugify(name).split("_"))
        if apart is not None:
            pieces = apart
            break
    for form in _known_forms(suffix):
        run = form.split("_")
        if any(_without_the_first_run(piece, run) is not None for piece in pieces):
            return form
    return None


def _new_entity_words(suffix: str) -> str:
    """Return the wording a new entity's base gets for `suffix`."""
    return NEW_ENTITY_ID_SUFFIX_WORDS.get(suffix, suffix.replace("_", " "))


def _base_of_the_words(words: str, device_name: str, *, device_in_front: bool) -> str:
    """Return `words` as a base, or nothing where the device's name in front of them ends on them."""
    if device_in_front and slugify(device_name).endswith(f"_{slugify(words)}"):
        return ""
    return words


def _leaves_the_suffix_out(device_name: str, suffix: str) -> bool:
    """Report whether a device of this name is composed with the suffix left out, in some wording.

    The omission rule of `_base_of_the_words`, for each wording the suffix has
    carried: "Current Power" ends on `power` after another word, and "Power"
    ends on nothing.
    """
    return any(
        _base_of_the_words(words, device_name, device_in_front=True) == ""
        for words in suffix_wordings(suffix)
    )


def _base_stopping_at_the_device(
    object_id: str,
    device_name: str,
    suffix: str,
    label_alone: Callable[[str], str | None] | None,
) -> str:
    """Return the base of an id that ends on the device's name, with the device in front of it.

    Empty, for the id stops there, unless the id is the entity's label alone
    (`label_alone`): that was composed with no device in front, so its base is
    the label's words. Where the device's name leaves the suffix out, the id
    reads either way and stays empty, which is what a new entity on that
    device gets, and the label is not composed.
    """
    if label_alone is None or _leaves_the_suffix_out(device_name, suffix):
        return ""
    words = label_alone(object_id)
    return "" if words is None else words


def _base_an_id_spells(
    object_id: str,
    id_identifier: str,
    device_name: str,
    suffix: str,
    *,
    device_then_entity: bool,
    device_in_front: bool,
    label_alone: Callable[[str], str | None] | None,
) -> str | None:
    """Return the base one object id reads as, or None where nothing here reads it.

    The readings `circuit_object_id_suffix` describes, of `object_id` as it is
    handed in. Nothing reads an id that names neither the device nor the
    identifier and spells no form of the suffix.
    """
    by_device = _read_after_the_name(
        object_id, device_name, suffix, may_stop_at_the_name=device_then_entity
    )
    by_identifier = _read_after_the_name(
        object_id, id_identifier, suffix, may_stop_at_the_name=device_then_entity
    )
    form: str | None = None
    # `>=`, so the device wins a tie.
    if by_device is not None and (
        by_identifier is None or by_device.tail_length >= by_identifier.tail_length
    ):
        if by_device.form:
            return by_device.form.replace("_", " ")
        if device_in_front:
            return _base_stopping_at_the_device(object_id, device_name, suffix, label_alone)
    elif by_identifier is not None:
        form = by_identifier.form

    if form is None:
        form = _existing_suffix_form(object_id, suffix)
    if form is None and not device_then_entity:
        form = _form_spelled_apart_from_the_name(object_id, (device_name, id_identifier), suffix)
    if form is None and by_device is None and by_identifier is None:
        return None
    return _base_of_the_words(
        form.replace("_", " ") if form else _new_entity_words(suffix),
        device_name,
        device_in_front=device_in_front,
    )


def circuit_object_id_suffix(
    id_identifier: str,
    device_name: str,
    suffix: str,
    existing_entity_id: str | None,
    *,
    device_then_entity: bool = True,
    device_in_front: bool = True,
    label_alone: Callable[[str], str | None] | None = None,
) -> str:
    """Return the base for one circuit entity on its circuit's device: the suffix alone.

    Home Assistant composes the circuit's name from the device, so the base
    carries only the suffix words (`power`, `energy consumed`). It is empty --
    Core then composes the id from the other parts of the user's entity-id
    format, the area and the devices under the default one -- in two cases: the existing id
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
    ("breaker", "circuit_priority"). `label_alone`, for an entity with no area
    of its own, tells whether an id is its label alone (below).

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

    An id that is the form and nothing else ends on it too. Under a format
    with no area part it is the label-only id an entity with an area of its
    own accepts, `sensor.energy_consumed`, and once the area is cleared it
    names neither name. Read as ending on no form, under `(device, entity)` it
    was offered `sensor.refrigerator_consumed_energy`.

    An id that ends on the device's name is not read as stopping there where
    it is the entity's label alone: the id Core composes for the entity in an
    area of its own, that area and the label with no device in front, which
    the user may have accepted and which stays the entity's id once the area
    is taken off it. `label_alone` gives, for an id, the wording it spells as
    the label alone; `circuit_object_ids` supplies it, composing the label in
    each area the registry holds. Composed with no device in front,
    the label alone does not stop at the device's name, so its base is the
    label's words. On a circuit named "Power", under Core's default format,
    `sensor.garage_power` is that label in Garage, and it ends on the device's
    name too. Read as stopping there, it was offered `sensor.span_panel_power`
    once its area was cleared, where the circuit's own id, and a new
    entity's, is `sensor.span_panel_power_power`. Where the device's name
    leaves the suffix out, as "Current Power" does, the id reads either way,
    and the stop is kept, since the empty base is what a new entity on that
    device gets; the label is not composed then. The label is asked about
    only where it decides, since composing it takes every area.

    Without `label_alone`, or where it answers nothing, such an id stops at
    the name. The id of a circuit renamed "Power" in the SPAN app has the same
    shape, `sensor.span_panel_solar_power`, and is no label in any area, so it
    keeps being offered `sensor.span_panel_power`.

    The omission test carries a leading underscore because the builder's did: a
    circuit named exactly "Power" is not a repetition of anything and kept both
    halves, `..._power_power`.

    All of that reads an id's tail, which is where the entity's part is while
    `device_then_entity`: the user's entity-id format ends with the device and
    then the entity, as the default does and as every id this integration
    preset did. The format is the user's to reorder, and under another order
    an id Core composes ends wherever that order ends, on the device's name
    under `(entity, device)`. So where the format is another:

    - an id that ends on a name is not read as stopping there. Read so,
      `sensor.power_kitchen_outlets` gave the empty base, and every entity of
      the circuit was offered the device's bare id;
    - an id that does spell a name and then a form is still read that way. It
      is one a release preset, or one composed before the format was changed;
    - otherwise the form is the one the id spells as whole words anywhere
      outside the name it carries (`_form_spelled_apart_from_the_name`), and
      the omission is decided from `device_name`, as for a new entity.

    An id Core numbered ends on the number: where the id it composed is
    taken, as it is for every circuit after the first of one name, Core
    appends `_2`, `_3` and on (`without_core_number`). None of the readings
    above finds a name or a form at such an end, so where the id as it stands
    spells nothing they read, the id without the number is read instead.
    Read only as it stands, `..._refrigerator_energy_consumed_2` on a circuit
    renamed in the SPAN app was offered `consumed energy`, a wording it never
    had. The id as it stands comes first because a name can end on a number
    of its own, and under a format ending with the device every id ends on the
    device's name: under `(entity, device)` a circuit named "Solar Power 2" has
    `sensor.current_power_solar_power_2`, which without its `2` would be read
    for the name's `power`.

    `circuit_object_ids.circuit_object_id_base` asks first which wording
    reproduces the id under the format as it stands, and comes here for the
    ids none does.

    Every omission above rests on Core composing the device's name in front of
    the base, which it does while `device_in_front`: for an entity with no
    area of its own. One that has an area is composed from the area and the
    base alone, so its base is never empty. Left out, a "Solar Power" sensor
    in the area Backyard was offered `sensor.backyard`, and under a format
    with no area part the platform and the unique id, Core's fallback for an
    id with nothing in it. The words are the form its id spells after the
    name, or failing that at its end, so an id that stops at "Solar Power" is
    read for the `power` it ends on and is offered `sensor.backyard_power`.
    """
    if existing_entity_id is not None:
        object_id = existing_entity_id.split(".", 1)[-1]
        unnumbered = without_core_number(object_id)
        for read in (object_id,) if unnumbered is None else (object_id, unnumbered):
            base = _base_an_id_spells(
                read,
                id_identifier,
                device_name,
                suffix,
                device_then_entity=device_then_entity,
                device_in_front=device_in_front,
                label_alone=label_alone,
            )
            if base is not None:
                return base
    return _base_of_the_words(
        _new_entity_words(suffix), device_name, device_in_front=device_in_front
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


def trim_leading_circuit_name(name: str, circuit_name: str) -> str | None:
    """Return a user-set name without the circuit name it starts with, or None if it starts with none.

    A circuit's device carries the circuit's name, and Home Assistant puts the
    device's name in front of an entity's, so a name the user wrote as "Kitchen
    Outlets Power usage" would display the circuit twice. The conversion to
    circuit devices stores it as "Power usage".

    The rule is Home Assistant's own, the one its registry migration applied to
    names that began with their device's: a copy of
    `entity_registry._async_strip_prefix_from_entity_name`, which is private.
    `tests/test_naming.py` holds this copy to that function.

    - Case is ignored, by case folding.
    - At least one of a space, a hyphen or a colon must follow the circuit's
      name, so "Kitchen OutletsPower" is not trimmed.
    - A first word left in lower case is capitalized, and one in mixed case,
      such as "iPhone", is kept as written.
    - A name that is the circuit's name and nothing more answers `""`: the
      entity is then named by its device alone, as it was displayed before.
    """
    if not name or not circuit_name:
        return None

    folded_circuit_name = circuit_name.casefold()
    folded_length = len(folded_circuit_name)

    candidate = name[:folded_length]
    matched_length = len(candidate)
    candidate = candidate.casefold()

    if not candidate.startswith(folded_circuit_name):
        return None

    # Case folding can lengthen a string ("ß" folds to "ss"), so the folded
    # match may cover fewer of the name's characters than it has itself.
    excess = len(candidate) - folded_length
    while excess > 0:
        matched_length -= 1
        excess -= len(name[matched_length].casefold())

    # The match ended inside one character's folding, so it is not a prefix.
    if excess < 0:
        return None

    rest = name[matched_length:].lstrip(" -:")

    if not rest:
        return ""

    # Nothing was stripped, so no separator followed the circuit's name.
    if len(rest) == len(name) - matched_length:
        return None

    first_word = rest.partition(" ")[0]
    if not first_word.islower():
        return rest
    return rest[0].upper() + rest[1:]
