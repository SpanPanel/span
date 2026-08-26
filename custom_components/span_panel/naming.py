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


def _existing_suffix_form(existing_entity_id: str | None, suffix: str) -> str | None:
    """Return the suffix form an existing id carries, or None if it carries none we know."""
    if existing_entity_id is None:
        return None
    object_id = existing_entity_id.split(".", 1)[-1]
    # Longest first so `current_power` is not read as `power`.
    for form in sorted(ENTITY_ID_SUFFIX_FORMS.get(suffix, ()), key=len, reverse=True):
        if object_id.endswith(f"_{form}"):
            return form
    return None


def circuit_object_id_base(identifier: str, suffix: str, existing_entity_id: str | None) -> str:
    """Return the base for one circuit entity.

    `identifier` is the naming-flag half (`Circuit 15`, `Kitchen Outlets`,
    `Unmapped Tab 32`); `suffix` is the canonical suffix from
    `id_builder.get_user_friendly_suffix`. Words are omitted when the
    identifier already ends with them, which is what the preset builder did
    for a circuit named "Solar Power".
    """
    form = _existing_suffix_form(existing_entity_id, suffix)
    words = (
        form.replace("_", " ")
        if form
        else NEW_ENTITY_ID_SUFFIX_WORDS.get(suffix, suffix.replace("_", " "))
    )
    if slugify(identifier).endswith(slugify(words)):
        return identifier
    return f"{identifier} {words}"
