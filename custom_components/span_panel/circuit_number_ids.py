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

It does not exempt the entity from the user's entity-id format. Core has no
public function that composes an id with another name in the device's place,
so `compose_circuit_number_object_id` mirrors the private one
(`entity_registry._async_get_full_entity_name`) from Core's public parts: the
saved format, the area, the floor and the panel's name. A test holds it to
Core's own answer for every format and area case, so a change in Core fails
there on the next Home Assistant bump.
"""

from __future__ import annotations

from typing import Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    floor_registry as fr,
)
from homeassistant.helpers.entity_registry import EntityNamePart

from .const import DOMAIN

_DEFAULT_ENTITY_ID_PARTS: Final = (
    EntityNamePart.AREA,
    EntityNamePart.PARENT_DEVICE,
    EntityNamePart.DEVICE,
    EntityNamePart.ENTITY,
)
"""The format Core composes with while the user has saved none."""


def compose_circuit_number_object_id(
    hass: HomeAssistant,
    *,
    panel_device_id: str,
    circuit_device_identifier: tuple[str, str],
    circuit_number: str,
    suffix_words: str,
    config_entry_id: str,
) -> str:
    """Return the object id Core would compose were the circuit's device named `circuit_number`.

    The parts, joined by a space in the order of the user's entity-id format
    and not yet slugified, which is how Core hands its own to
    `async_get_available_entity_id`. A new entity has no area and no name of
    its own, so each part is what Core reads for one:

    - the area is the circuit device's, or the panel's where that device has
      none or is not registered yet, which is the case for a new circuit's
      first entities: Core registers the device as it adds them, without an
      area;
    - the floor is that area's;
    - the parent device is the panel, by the name the user gave it ahead of
      its own, and is named only while the circuit's device has no area of its
      own, as Core names a child device's parent;
    - the device is `circuit_number`, in place of the name the circuit's
      device carries;
    - the entity is `suffix_words`, the entity's base.

    A part the format leaves out, or that has nothing to say, is skipped.
    """
    parts = er.async_get(hass).settings.entity_id_parts
    if parts is None:
        parts = _DEFAULT_ENTITY_ID_PARTS

    device_registry = dr.async_get(hass)
    panel = device_registry.async_get(panel_device_id, include_child_devices=False)
    circuit_device = device_registry.async_get_child_device_by_identifier(
        circuit_device_identifier, config_entry_id
    )
    own_area_id = circuit_device.area_id if circuit_device is not None else None

    panel_name: str | None = None
    area_id = own_area_id
    if own_area_id is None and panel is not None:
        panel_name = panel.name_by_user or panel.name
        area_id = panel.area_id

    area_name: str | None = None
    floor_name: str | None = None
    if area_id is not None and (area := ar.async_get(hass).async_get_area(area_id)) is not None:
        area_name = area.name
        if (
            area.floor_id is not None
            and (floor := fr.async_get(hass).async_get_floor(area.floor_id)) is not None
        ):
            floor_name = floor.name

    part_names: dict[EntityNamePart, str | None] = {
        EntityNamePart.AREA: area_name,
        EntityNamePart.DEVICE: circuit_number,
        EntityNamePart.ENTITY: suffix_words,
        EntityNamePart.FLOOR: floor_name,
        EntityNamePart.PARENT_DEVICE: panel_name,
    }
    return " ".join(part_name for part in parts if (part_name := part_names[part]))


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
