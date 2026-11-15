"""Move an existing install's circuit entities onto their circuits' own devices.

Releases before 2.3.0 put every circuit entity on the panel's device and named
it with the circuit in front, "Kitchen Outlets Power". Each circuit now has a
device of its own, a child of the panel's and named by the circuit, and its
entities carry only their label, "Power". The platforms build them that way,
and this module is what carries an existing registry across.

It runs at setup, once the panel's data has loaded and before the platforms are
forwarded, so every entity is added onto its circuit's device. It is not a
config-entry migration, because a migration has only the registry: no unique-id
shape tells a circuit's sensors from panel sensors such as
`span_{serial}_current_power`, and a circuit's id is recognised only against the
ids the panel publishes. Here a row is a circuit's entity when its unique id is
one the platforms build for a circuit in the snapshot (`_circuit_entities`).

For each circuit with an entity still on the panel's device, in this order:

1. each entity's displayed name is recorded, before anything changes;
2. the circuit's device is created, as the platforms would create it;
3. names an older release wrote into the registry are handed back, before
   anything reads them as the user's;
4. the circuit's area moves to its device, where that cannot put a breaker in
   a room (`_area_for_the_device`);
5. a user's name that starts with the circuit's name is trimmed, since the
   device now says it;
6. each entity is moved, renamed to its label and given its area and name in
   one registry update.

It acts only on entities still on the panel's device, so it does nothing once
done, a conversion cut short finishes at the next setup, and it runs at every
setup while releases before 2.3.0 remain upgrade paths. No entity ID and no
unique ID changes.

Left where they are:

- an entity of a circuit the panel does not publish, which stays on the
  panel's device with its name and area untouched;
- a circuit-scoped extension entity, which is never re-homed (`extension`);
- a charger's feed circuit's sensors, which belong on the charger's device;
- any other row whose unique id names a circuit without being one the
  integration builds for it, because nothing says what its label would be.

`conversion_repairs` turns the report into the notice that tells the user what
changed, after the platforms are set up.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from span_panel_api import SpanPanelSnapshot

from .const import DOMAIN
from .entity_resolver import entity_id_in_entry
from .extension import is_extension_unique_id
from .id_builder import build_circuit_unique_id, build_select_unique_id, build_switch_unique_id
from .naming import release_registry_name_written_by_older_release, trim_leading_circuit_name
from .runtime import SpanPanelConfigEntry
from .select import CIRCUIT_PRIORITY_DESCRIPTION
from .sensor_base import description_label
from .sensor_circuit import circuit_sensor_api_key
from .sensor_definitions import (
    CIRCUIT_BREAKER_RATING_SENSOR,
    CIRCUIT_CURRENT_SENSOR,
    CIRCUIT_SENSORS,
)
from .switch import BREAKER_LABEL
from .util import circuit_device_info, effective_circuit_device_name

_LOGGER = logging.getLogger(__name__)

_POWER_WIRE_KEY: Final = "instantPowerW"
"""The wire key a circuit's power sensor's unique id is built from."""


@dataclass(frozen=True)
class ConvertedEntity:
    """One entity the conversion moved to its circuit's device."""

    entity_id: str
    name_before: str
    """The name Home Assistant displayed it under before the move."""


@dataclass(frozen=True)
class AreaChange:
    """One entity the conversion moved to another area, by taking its own area away.

    Recorded only where the area the entity is in changed. An entity whose own
    area was the one its circuit's device took is in the same area as before.
    """

    entity_id: str
    area_before: str
    """The id of the area the entity had of its own."""

    area_after: str | None
    """The id of its circuit's device's area, which it now inherits."""


@dataclass(frozen=True)
class ConversionReport:
    """What one run of the conversion changed."""

    entities: tuple[ConvertedEntity, ...]
    area_changes: tuple[AreaChange, ...]
    circuits_left_in_areas: tuple[str, ...]
    """The device names of the circuits whose entities kept areas of their own.

    Those whose breaker switch has no area: an area moved to the device would
    reach the switch.
    """

    @property
    def empty(self) -> bool:
        """Whether the run converted nothing, as every run after the first does."""
        return not self.entities


@dataclass(frozen=True, slots=True)
class _CircuitEntity:
    """One of the entities the integration builds for a circuit, on the circuit's device."""

    circuit_id: str

    label: str
    """Its whole name on the circuit's device."""

    older_labels: tuple[str, ...] = ()
    """Labels it carried in older releases, which the registry may still hold."""


def _circuit_entities(snapshot: SpanPanelSnapshot) -> dict[tuple[str, str], _CircuitEntity]:
    """Return every entity that belongs on a circuit's device, by domain and unique id.

    Built from the snapshot's circuits with the builders the platforms use, so
    a registry row is recognised exactly where a platform would find it again:
    by its whole unique id, never by the shape of one. That settles which
    circuit a row names, as `id_builder.match_circuit_id` does, and which of
    the circuit's entities it is, which says what its label becomes.

    A charger's feed circuit's sensors are not among them. They go on the
    charger's device, where the sensor platform puts them, and only the feed
    circuit's switch and select are its own device's.
    """
    serial = snapshot.serial_number
    charger_feeds = {evse.feed_circuit_id for evse in snapshot.evse.values()}
    entities: dict[tuple[str, str], _CircuitEntity] = {}
    for circuit_id in snapshot.circuits:
        if circuit_id not in charger_feeds:
            for description in (
                *CIRCUIT_SENSORS,
                CIRCUIT_CURRENT_SENSOR,
                CIRCUIT_BREAKER_RATING_SENSOR,
            ):
                unique_id = build_circuit_unique_id(
                    serial, circuit_id, circuit_sensor_api_key(description.key)
                )
                entities["sensor", unique_id] = _CircuitEntity(
                    circuit_id, description_label(description), description.legacy_names
                )
        entities["switch", build_switch_unique_id(serial, circuit_id)] = _CircuitEntity(
            circuit_id, BREAKER_LABEL
        )
        entities["select", build_select_unique_id(serial, circuit_id)] = _CircuitEntity(
            circuit_id, CIRCUIT_PRIORITY_DESCRIPTION.name
        )
    return entities


def _rows_to_convert(
    registry: er.EntityRegistry,
    entry_id: str,
    snapshot: SpanPanelSnapshot,
    panel_device_id: str,
) -> dict[str, dict[str, _CircuitEntity]]:
    """Return the circuit entities still on the panel's device: circuit id to entity id to entity.

    A row is one of them when it belongs to this entry, sits on the panel's
    device and is one of the entities the integration builds for a circuit the
    snapshot holds. Disabled entities are included: the registry keeps their
    rows, and a platform that never adds them would never move them. An
    extension entity is ruled out by name, though no id the integration builds
    for a circuit is an extension's: it is never re-homed, whatever it names.

    In the registry's own order, the order it stores its rows in, which the
    area rule reads. The registry's per-entry index is not in that order: an
    update moves a row to its end, so read through it the first area among a
    circuit's entities would be the one on whichever was edited longest ago.
    """
    circuit_entities = _circuit_entities(snapshot)
    selected: dict[str, dict[str, _CircuitEntity]] = {}
    for row in registry.entities.values():
        if (
            row.config_entry_id != entry_id
            or row.device_id != panel_device_id
            or is_extension_unique_id(row.unique_id)
        ):
            continue
        entity = circuit_entities.get((row.domain, row.unique_id))
        if entity is not None:
            selected.setdefault(entity.circuit_id, {})[row.entity_id] = entity
    return selected


def _circuit_switch(
    registry: er.EntityRegistry, entry_id: str, serial: str, circuit_id: str
) -> er.RegistryEntry | er.DeletedRegistryEntry | None:
    """Return the circuit's breaker switch row, live or withdrawn, or None if it has none.

    The integration removes a breaker switch when the panel stops offering
    control, and Home Assistant restores the removed row, area included, when
    control returns. So a row among the registry's deleted entities is still
    the circuit's switch, and its area still the one the switch will be in.
    """
    unique_id = build_switch_unique_id(serial, circuit_id)
    entity_id = entity_id_in_entry(registry, entry_id, "switch", unique_id)
    if entity_id is not None:
        return registry.async_get(entity_id)
    return registry.deleted_entities.get(("switch", DOMAIN, unique_id))


def _area_for_the_device(
    switch: er.RegistryEntry | er.DeletedRegistryEntry | None,
    rows: list[er.RegistryEntry],
    power_unique_id: str,
) -> str | None:
    """Return the area a circuit's device takes from its entities, or None if none moves.

    The breaker switch decides wherever there is one. Entities with no area of
    their own inherit their device's, and an area-wide turn-off reaches every
    switch in the area, so moving another entity's area to the device would put
    the breaker in that room.

    1. A switch with an area of its own: the device takes that area.
    2. No switch: the device takes the power sensor's area, or failing that the
       first area among the circuit's other entities, in the registry's order.
    3. A switch with no area of its own: nothing moves.
    """
    if switch is not None:
        return switch.area_id
    power = next((row for row in rows if row.unique_id == power_unique_id), None)
    if power is not None and power.area_id is not None:
        return power.area_id
    return next((row.area_id for row in rows if row.area_id is not None), None)


@callback
def async_convert_circuit_entities(
    hass: HomeAssistant,
    entry: SpanPanelConfigEntry,
    snapshot: SpanPanelSnapshot,
    panel_device_id: str,
) -> ConversionReport:
    """Move this entry's circuit entities from the panel's device to their circuits' devices.

    `snapshot` says which circuits there are, and `panel_device_id` is the
    registry id of the panel's device, which must already be registered. One
    callback with no await in it, so no circuit is ever seen half converted.
    Returns what changed, for `conversion_repairs.async_raise_conversion_notice`
    to report once the platforms are set up.
    """
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    serial = snapshot.serial_number

    converted: list[ConvertedEntity] = []
    area_changes: list[AreaChange] = []
    circuits_left_in_areas: list[str] = []

    selected = _rows_to_convert(entity_registry, entry.entry_id, snapshot, panel_device_id)
    for circuit_id, entities in selected.items():
        circuit = snapshot.circuits[circuit_id]

        names_before = {
            entity_id: er.async_get_full_entity_name(hass, row)
            for entity_id in entities
            if (row := entity_registry.async_get(entity_id)) is not None
        }

        device = device_registry.async_get_or_create_child(
            config_entry_id=entry.entry_id,
            **circuit_device_info(serial, circuit, circuit_id, panel_device_id=panel_device_id),
        )
        # The name Home Assistant puts in front of each entity's: the user's own
        # name for the device where a conversion cut short left one to rename.
        device_name = effective_circuit_device_name(
            device_registry, serial, circuit, circuit_id, config_entry_id=entry.entry_id
        )

        for entity_id, entity in entities.items():
            release_registry_name_written_by_older_release(
                entity_registry, entity_id, circuit.name, (entity.label, *entity.older_labels)
            )

        rows = [
            row
            for entity_id in entities
            if (row := entity_registry.async_get(entity_id)) is not None
        ]
        switch = _circuit_switch(entity_registry, entry.entry_id, serial, circuit_id)
        device_area = _area_for_the_device(
            switch, rows, build_circuit_unique_id(serial, circuit_id, _POWER_WIRE_KEY)
        )
        if device_area is not None:
            # The rule decides even where the device already has an area, which
            # only a conversion cut short can have left it: the area is the one
            # the breaker switch is in already, or the circuit has no switch.
            device_registry.async_update_child_device(device.id, area_id=device_area)
        elif switch is not None and any(row.area_id is not None for row in rows):
            circuits_left_in_areas.append(device_name)

        for row in rows:
            # Once the device has the circuit's area, no entity keeps one of its
            # own: it would display without its device, as just "Power".
            own_area = row.area_id
            area_id = None if device_area is not None else own_area
            if device_area is not None and own_area is not None and own_area != device_area:
                area_changes.append(AreaChange(row.entity_id, own_area, device_area))

            # An entity that keeps an area of its own displays without its
            # device, so its name is whole as it stands. Home Assistant's own
            # migration skips it for the same reason, and refuses an edit that
            # would leave such an entity with no name of its own.
            name = row.name
            if name and area_id is None:
                trimmed = trim_leading_circuit_name(name, device_name)
                if trimmed is not None:
                    name = trimmed

            entity_registry.async_update_entity(
                row.entity_id,
                device_id=device.id,
                original_name=entities[row.entity_id].label,
                area_id=area_id,
                name=name,
            )
            converted.append(ConvertedEntity(row.entity_id, names_before[row.entity_id]))

    if converted:
        _LOGGER.info(
            "Moved %d circuit entities of %s to the devices of %d circuits",
            len(converted),
            entry.title,
            len(selected),
        )
    return ConversionReport(tuple(converted), tuple(area_changes), tuple(circuits_left_in_areas))
