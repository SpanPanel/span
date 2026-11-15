"""The id Home Assistant composes for a circuit entity, and the base that reproduces it.

A circuit entity hands Core a base, its suffix words or nothing
(`naming.circuit_object_id_suffix`), and Core composes the id from the user's
entity-id format: the area, the panel's device, the circuit's device and the
base, in whatever order and selection the user saved. This module is where the
integration works with that composition instead of assuming its shape.

## The composer

Core has no public function that composes an id with another name in the
device's place, so `compose_circuit_object_id` mirrors the private one
(`entity_registry._async_get_full_entity_name`) from Core's public parts: the
saved format, the area, the floor and the panel's name. A test holds it to
Core's own answer for every format and area case, so a change in Core fails
there on the next Home Assistant bump. It has two callers: the id
circuit-numbers mode creates an entity with, where the device is named by the
circuit's number (`circuit_number_ids`), and the read-back below, where it is
named as it will be once the entity is added.

It composes from the parts it knows (`COMPOSED_PARTS`), which are all Core has
today, and a test holds the two sets equal. A format that holds any other
part, as one saved under a later Home Assistant could, gets no answer: an id
composed without a part the user asked for would not be the id Core composes.
The entity is then created without a preset, with the id Core gives it, and an
existing id is read by its tail.

## The base that reproduces an id

An entity that has an id is given the base under which Core composes that same
id, so "Recreate entity IDs" offers it nothing (R1). `circuit_object_id_base`
finds it by asking: each wording the suffix has shipped with, and the empty
base, is composed under the format, the areas and the names as they stand, and
the one that spells the id the entity has is its base. That holds for any
format the user saves, because nothing is assumed about where in the id the
entity's part sits.

No wording reproduces an id Core would not compose today: one an older release
preset, one composed before a circuit, a device or an area was renamed or the
format was changed, the circuit-number preset on a circuit that has a name.
Those are read by `naming.circuit_object_id_suffix`, which is told whether the
format ends with the device and then the entity, the shape it reads a tail in.

An entity with an area of its own is composed as Core composes it: from that
area and the base, with neither device in front. Its base is therefore never
empty. The empty base is for an id that stops at the device's name, and with
no device in front it would leave Core the area alone to compose from, or
nothing at all.

## Keeping the base current

The base depends on the name of the circuit's device: it is empty where the
name already ends on the suffix words, and an id is read against the name. It
depends too on whether the entity has an area of its own, which decides
whether Core puts that name in front at all. A
rename in the SPAN app reloads the entry, which builds every entity again. A
rename of the device in Home Assistant reloads nothing, and Core composes
"Recreate entity IDs" from the row as stored, so a base decided for the old
name would be offered under the new one: a circuit named "Solar Power", on
`sensor.span_panel_solar_power` with an empty base, renamed "Roof Array" would
be offered `sensor.span_panel_roof_array`, an id no entity on that device is
created with.

An area set on an entity, or taken off it, reloads nothing either: a "Solar
Power" sensor given the area Backyard would be offered `sensor.backyard`, and
one whose area was taken away `sensor.span_panel_solar_power_power`.

So each circuit entity records what its base is worked out from
(`CircuitIdBase`) when it is built, enabled or not, and
`async_keep_circuit_id_bases_current` works the bases of a circuit out again
when its device's `name_by_user` changes, and an entity's when its own area
does, and stores those that differ. It does so inside the registry's update
event, before the change has returned, because Home Assistant asks whether to
rename the device's entity ids straight after a rename. A reload would answer
too late.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import logging
from typing import Final

from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    floor_registry as fr,
)
from homeassistant.helpers.entity_registry import EntityNamePart
from homeassistant.util import slugify
from span_panel_api import SpanCircuitSnapshot

from .const import DOMAIN
from .id_builder import build_circuit_device_identifier
from .naming import circuit_object_id_suffix, suffix_wordings
from .runtime import SpanPanelConfigEntry, loaded_runtime_data
from .util import circuit_id_of_device, effective_circuit_device_name

_LOGGER = logging.getLogger(__name__)

COMPOSED_PARTS: Final = frozenset(
    {
        EntityNamePart.AREA,
        EntityNamePart.DEVICE,
        EntityNamePart.ENTITY,
        EntityNamePart.FLOOR,
        EntityNamePart.PARENT_DEVICE,
    }
)
"""The parts of an entity-id format the composer knows how to name.

Every part Core has today. A test holds this to Core's enum, so a part a later
Home Assistant adds fails the suite at the bump instead of passing unnoticed.
"""

_DEFAULT_ENTITY_ID_PARTS: Final = (
    EntityNamePart.AREA,
    EntityNamePart.PARENT_DEVICE,
    EntityNamePart.DEVICE,
    EntityNamePart.ENTITY,
)
"""The format Core composes with while the user has saved none."""

_DEVICE_THEN_ENTITY: Final = (EntityNamePart.DEVICE, EntityNamePart.ENTITY)
"""How the default format ends, and the shape of every id a release preset."""


@dataclass(frozen=True, slots=True)
class CircuitIdBase:
    """What one circuit entity's id base is worked out from, kept to work it out again."""

    domain: str
    unique_id: str

    id_identifier: str
    """The name ids were built from before the circuit had a device: its tabs or its name."""

    suffix: str
    """The entity's canonical suffix, as `naming.ENTITY_ID_SUFFIX_FORMS` keys it."""


def entity_id_parts(hass: HomeAssistant) -> tuple[EntityNamePart, ...]:
    """Return the entity-id format in force: the one the user saved, or Core's default."""
    parts = er.async_get(hass).settings.entity_id_parts
    return _DEFAULT_ENTITY_ID_PARTS if parts is None else tuple(parts)


def compose_circuit_object_id(
    hass: HomeAssistant,
    *,
    panel_device_id: str,
    circuit_device_identifier: tuple[str, str],
    device_name: str,
    suffix_words: str,
    config_entry_id: str,
    entity_area_id: str | None = None,
) -> str | None:
    """Return the object id Core would compose were the circuit's device named `device_name`.

    The parts, joined by a space in the order of the user's entity-id format
    and not yet slugified, which is how Core hands its own to
    `async_get_available_entity_id`. For an entity with no name of its own.
    For one with no area of its own either, as a new one is, each part is what
    Core reads for one:

    - the area is the circuit device's, or the panel's where that device has
      none or is not registered yet, which is the case for a new circuit's
      first entities: Core registers the device as it adds them, without an
      area;
    - the floor is that area's;
    - the parent device is the panel, by the name the user gave it ahead of
      its own, and is named only while the circuit's device has no area of its
      own, as Core names a child device's parent;
    - the device is `device_name`, in place of the name the circuit's device
      carries in the registry;
    - the entity is `suffix_words`, the entity's base.

    For an entity in `entity_area_id`, an area of its own, Core names neither
    device: the area is the entity's, the floor is that area's, and the
    entity's base is the only other part.

    A part the format leaves out, or that has nothing to say, is skipped.

    None where the format holds a part that is not among `COMPOSED_PARTS`:
    what Core composes from it is not known here.
    """
    parts = entity_id_parts(hass)
    if unknown := [part for part in parts if part not in COMPOSED_PARTS]:
        _LOGGER.debug(
            "The entity ID format holds parts this integration cannot compose (%s),"
            " so no ID is composed for a circuit entity under it",
            ", ".join(str(part) for part in unknown),
        )
        return None

    device_registry = dr.async_get(hass)
    panel = device_registry.async_get(panel_device_id, include_child_devices=False)
    circuit_device = device_registry.async_get_child_device_by_identifier(
        circuit_device_identifier, config_entry_id
    )
    device_area_id = circuit_device.area_id if circuit_device is not None else None

    composed_device_name: str | None = None
    panel_name: str | None = None
    area_id = entity_area_id
    if entity_area_id is None:
        composed_device_name = device_name
        area_id = device_area_id
        if device_area_id is None and panel is not None:
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
        EntityNamePart.DEVICE: composed_device_name,
        EntityNamePart.ENTITY: suffix_words,
        EntityNamePart.FLOOR: floor_name,
        EntityNamePart.PARENT_DEVICE: panel_name,
    }
    return " ".join(part_name for part in parts if (part_name := part_names.get(part)))


def _is_the_id_core_registers(object_id: str, composed: str, *, numbered: bool) -> bool:
    """Report whether Core registers `composed` as `object_id`.

    As it stands, or with `numbered`, under the number Core appends where an
    id is taken: two circuits named "Lights" are `..._lights_power` and
    `..._lights_power_2`.
    """
    slug = slugify(composed)
    if not composed or not slug:
        return False
    if not numbered:
        return object_id == slug
    head, _, number = object_id.rpartition("_")
    return head == slug and number.isdecimal() and int(number) > 1


def _base_reproducing(object_id: str, composed_by_base: dict[str, str]) -> str | None:
    """Return the base whose composition is the id an entity has, or None if none is.

    An id that is a composition as it stands outranks one that is a
    composition with a number after it.
    """
    for numbered in (False, True):
        for base, composed in composed_by_base.items():
            if _is_the_id_core_registers(object_id, composed, numbered=numbered):
                return base
    return None


def circuit_object_id_base(
    hass: HomeAssistant,
    *,
    panel_device_id: str,
    config_entry_id: str,
    serial: str,
    circuit: SpanCircuitSnapshot,
    circuit_id: str,
    recipe: CircuitIdBase,
    existing_entity_id: str | None,
) -> str:
    """Return the base of a circuit entity's id on its circuit's device: the suffix, or nothing.

    For an entity that has an id, the base under which Core composes that id
    from the circuit's device as it will be named once the entity is added
    (`util.effective_circuit_device_name`), under the user's entity-id format.
    Where no base does, where the format holds a part the composer does not
    know, and for an entity with no id yet, `naming.circuit_object_id_suffix`
    decides.

    The compositions tried are for an entity with no name of its own. One with
    a name composes from the name, so its id is not reproduced here, and its
    base decides nothing it is offered beyond the wording it keeps.

    An entity with an area of its own is composed with no device in front, so
    the empty base is not among those tried for it and is never its answer:
    it is offered its label alone, as R1's own-area exception says, in the
    wording its id has.
    """
    device_name = effective_circuit_device_name(
        dr.async_get(hass), serial, circuit, circuit_id, config_entry_id=config_entry_id
    )
    entity_area_id: str | None = None
    if existing_entity_id is not None:
        row = er.async_get(hass).async_get(existing_entity_id)
        entity_area_id = row.area_id if row is not None else None
        wordings = suffix_wordings(recipe.suffix)
        circuit_device_identifier = (DOMAIN, build_circuit_device_identifier(serial, circuit_id))
        reproducing = _base_reproducing(
            existing_entity_id.split(".", 1)[-1],
            {
                base: composed
                for base in (wordings if entity_area_id is not None else (*wordings, ""))
                if (
                    composed := compose_circuit_object_id(
                        hass,
                        panel_device_id=panel_device_id,
                        circuit_device_identifier=circuit_device_identifier,
                        device_name=device_name,
                        suffix_words=base,
                        config_entry_id=config_entry_id,
                        entity_area_id=entity_area_id,
                    )
                )
                is not None
            },
        )
        if reproducing is not None:
            return reproducing
    return circuit_object_id_suffix(
        recipe.id_identifier,
        device_name,
        recipe.suffix,
        existing_entity_id,
        device_then_entity=entity_id_parts(hass)[-2:] == _DEVICE_THEN_ENTITY,
        device_in_front=entity_area_id is None,
    )


@callback
def async_refresh_circuit_id_bases(
    hass: HomeAssistant,
    *,
    panel_device_id: str,
    config_entry_id: str,
    serial: str,
    circuit: SpanCircuitSnapshot,
    circuit_id: str,
    recipes: Iterable[CircuitIdBase],
) -> None:
    """Work out the id bases of one circuit's entities again, and store those that changed.

    Stored by registering the entity again with its base, which is how the
    platform's own add refreshes a row: Core's update call takes no base. The
    row's id and everything else about it stay as they are.
    """
    registry = er.async_get(hass)
    for recipe in recipes:
        entity_id = registry.async_get_entity_id(recipe.domain, DOMAIN, recipe.unique_id)
        if entity_id is None or (row := registry.async_get(entity_id)) is None:
            continue
        base = circuit_object_id_base(
            hass,
            panel_device_id=panel_device_id,
            config_entry_id=config_entry_id,
            serial=serial,
            circuit=circuit,
            circuit_id=circuit_id,
            recipe=recipe,
            existing_entity_id=entity_id,
        )
        if row.object_id_base != base:
            registry.async_get_or_create(
                recipe.domain, DOMAIN, recipe.unique_id, object_id_base=base
            )


@callback
def async_keep_circuit_id_bases_current(
    hass: HomeAssistant, entry: SpanPanelConfigEntry
) -> CALLBACK_TYPE:
    """Refresh circuit entities' id bases when what they were decided from changes without a reload.

    Two changes do that, and each is refreshed inside the registry's own
    update event:

    - a circuit's device renamed in Home Assistant, which refreshes every
      entity of the circuit. Only a change of `name_by_user` counts: the
      device's own name changes with the circuit's in the SPAN app, which
      reloads the entry;
    - an area set on a circuit entity or taken off it, which refreshes that
      entity.

    Returns the call that stops it, for the entry's unload. The circuits are
    those of the snapshot the entry was set up from, which its entities were
    built from.
    """

    @callback
    def _refresh(device_id: str, *, only: tuple[str, str] | None = None) -> None:
        """Refresh the bases of the circuit whose device this is: all, or `only` (domain, unique id)."""
        runtime_data = loaded_runtime_data(entry)
        device = dr.async_get(hass).async_get(device_id)
        if runtime_data is None or not isinstance(device, dr.ChildDeviceEntry):
            return
        snapshot = runtime_data.setup_snapshot
        circuit_id = circuit_id_of_device(snapshot.serial_number, device)
        if circuit_id is None or (circuit := snapshot.circuits.get(circuit_id)) is None:
            return
        async_refresh_circuit_id_bases(
            hass,
            panel_device_id=runtime_data.panel_device_id,
            config_entry_id=entry.entry_id,
            serial=snapshot.serial_number,
            circuit=circuit,
            circuit_id=circuit_id,
            recipes=[
                recipe
                for recipe in runtime_data.circuit_id_bases.get(circuit_id, ())
                if only is None or (recipe.domain, recipe.unique_id) == only
            ],
        )

    @callback
    def _device_updated(event: Event[dr.EventDeviceRegistryUpdatedData]) -> None:
        if event.data["action"] != "update" or "name_by_user" not in event.data["changes"]:
            return
        _refresh(event.data["device_id"])

    @callback
    def _entity_updated(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        if event.data["action"] != "update" or "area_id" not in event.data["changes"]:
            return
        row = er.async_get(hass).async_get(event.data["entity_id"])
        if (
            row is None
            or row.platform != DOMAIN
            or row.config_entry_id != entry.entry_id
            or row.device_id is None
        ):
            return
        _refresh(row.device_id, only=(row.domain, row.unique_id))

    stop_listening = (
        hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, _device_updated),
        hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _entity_updated),
    )

    @callback
    def _stop() -> None:
        for stop in stop_listening:
            stop()

    return _stop
