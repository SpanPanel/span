"""The entity id circuit-numbers mode gives a circuit entity when it creates one.

A circuit's entities sit on a device named by the circuit, so Home Assistant
composes a new entity's id from the circuit's name. Circuit-numbers mode
promises an id that carries the breaker position instead, and keeps the
promise here: for an entity being created, and only then, the integration
presets the id Core would have composed had the circuit's device been named by
its number -- `sensor.span_panel_circuit_15_power`.

The preset is the one place this integration sets an `entity_id`, and it is
made once. Core stores a preset as the row's `suggested_object_id` and uses it
as it stands, and clears it the next time the entity is added, when the
integration presets nothing. From then on the row composes from the circuit's
device and the entity's base like every other, so "Recreate entity IDs" offers
the circuit's name, and the id stays where it is until the user accepts.

It does not exempt the entity from the user's entity-id format. The id is
composed under that format by `circuit_object_ids.compose_circuit_object_id`,
with the circuit's number in the device's place.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN


def preset_circuit_number_entity_id(
    hass: HomeAssistant, *, domain: str, unique_id: str, object_id: str
) -> str | None:
    """Return the entity id to create an entity with, or None for one that is not new.

    The id is `object_id` as Core would register it: slugified under `domain`,
    with Core's own suffix where the id is taken.

    None for a unique id the registry has a row for. An active row is an
    existing entity, whose id never moves (R5). A row in `deleted_entities` is
    an entity Core removed and remembers -- a control the panel withdrew, an
    entity the user deleted -- and Core restores the id it had, so a preset
    would only leave a stale suggestion on the restored row.

    None too for an empty `object_id`, which a format with nothing to say for
    this entity composes: Core has its own fallback for that.
    """
    registry = er.async_get(hass)
    if registry.async_get_entity_id(domain, DOMAIN, unique_id) is not None:
        return None
    if (domain, DOMAIN, unique_id) in registry.deleted_entities:
        return None
    if not object_id:
        return None
    return registry.async_get_available_entity_id(domain, object_id)
