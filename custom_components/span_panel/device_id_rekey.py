"""Carry a panel's circuits across a firmware update that gives them new device ids.

`device_id_migration` says which new id each old one pairs with. This module
re-keys, once, everything this integration keys by a circuit's id, so an
install keeps every entity, device and setting it had, with no visible step:

1. **Devices**, in place: each circuit's own child device and, on a panel with
   several inverters, each inverter's card a circuit feeds. The registry id,
   name, area and the rest of the user's customisations stay.
2. **Entities**, in place: every unique id built for the old id, built again for
   the new one with the same builder (`id_builder.circuit_entity_unique_ids`,
   `pv_binding.inverter_card_unique_ids`, `extension.extension_unique_id_prefix`),
   never parsed. The `entity_id`, history, statistics and customisations stay.
3. **Stores and notices**: each module that owns a circuit-keyed store re-keys
   it, by its own shape, before anything loads it.

**Names are kept, but where one is built from the raw id.** A circuit's
device and entities are named from what the panel publishes for it -- its
name, or its breaker positions -- and a remote CT's by `outside_meter_label`,
from the trait instance both its ids end in, in either mode; none of that
changes with the id. `helpers.construct_circuit_identifier_from_tabs` names a
circuit with no breaker positions `Circuit {circuit id}`, and from the first
setup on the new ids that is the new id. No capture reaches it. Three names
can:

- the device of a hosted circuit whose declared spaces carry no value, where
  the panel gives it no name and it is not a solar circuit
  (`helpers.circuit_device_name`), and with it its entities' names;
- the card of an inverter fed by a hosted circuit with no breaker positions,
  unless the install names circuits by name and the panel names that one
  (`helpers.resolve_pv_display_suffix`); an inverter a remote CT feeds is
  named by the remote CT's label;
- in circuit-numbers mode, the identifier a hosted circuit with no tabs names
  its entities by, whether the panel names it or not.

A hosted circuit's label (`helpers.construct_circuit_label`) follows the id
the same way, as does that of a circuit that has left the snapshot. Two
things keep it: the package-ratings store saves it as each package's label,
rewritten from the circuit at every reconcile, and as the label of each
removal it lists; and the recorder keeps the `meter_shared_with` and
`relay_shared_with` attributes it fills, in the history of the sensor or
switch that carries them.

**Run at every setup** (`async_setup_entry`), after the first refresh and the
hardware verdict and before anything loads, reconciles or registers circuit
state. It acts only where the registry or a store of this entry still holds an
old id, so a setup that migrated part of a panel finishes the job at the next,
which the coordinator's guard asks for as soon as the missing circuits arrive
(`holds_old_ids`).

**What a setup costs.** On a panel whose ids are opaque nothing pairs, and it
returns after the pairing. On a panel on the new ids the pairing is never
empty, so every setup looks again, and writes only what is still left to move:

- the registries: each pair's circuit device and inverter card are looked up
  by identifier, every unique id built for its old id is looked up, and this
  entry's entity rows are scanned once for extension prefixes;
- the stores: each circuit-keyed store is read from disk, and saved only when
  a value in it moved; the settings file is read under its lock;
- the notices' live view and the issue registry are checked in memory.

On a migrated install that is reads and nothing else: no registry entry,
store or notice is written.

Not a config-entry version step: that runs before setup, with no snapshot. No
version changes, because what this leaves is exactly what a release without it
builds on the new firmware.

**An earlier release running across the change** registered a second entity
for each circuit and new devices under the new ids. Those give way to the
originals, which hold the history: the newer devices' entities and the devices
are removed, each breaker switch with the area notice raised about it, then the
originals re-keyed. For a stored value the rule is the other way round
(`device_id_migration.rekeyed`).

**Split from `device_id_migration` on purpose.** This module imports every
store's owner, `extension` and `conversion_repairs` among them, and those import
the coordinator, directly or through the platforms, and the coordinator asks
`device_id_migration` on every snapshot. Merged,
the two would be an import cycle, so the pairing stays in that leaf and only
the re-keying lives here.

**Tombstones are not re-keyed.** Home Assistant offers no public way to, so a
control the panel withdrew before the change, or an entity or device the user
deleted, returns under its new id without its old customisations.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from span_panel_api import SpanPanelSnapshot

from . import (
    additions,
    conversion_repairs,
    curation,
    current_monitor,
    extension,
    frontend,
    graph_horizon,
    package_rating_manager,
    pv_binding,
)
from .const import DOMAIN
from .device_id_migration import HoldsOldIds, pair_device_ids, warn_of_trait_conflict
from .entity_resolver import entity_id_in_entry
from .helpers import circuit_device_name
from .id_builder import build_circuit_device_identifier, circuit_entity_unique_ids
from .runtime import SpanPanelConfigEntry
from .util import circuit_device, pv_inverter_device_identifier

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Moved:
    """What the device step did: how many devices moved, and what gave way."""

    moved: int
    devices_removed: int
    entities_removed: int
    """The entities of the devices that gave way, removed with them."""


@dataclass(frozen=True, slots=True)
class _Rekeyed:
    """What the entity step did: how many rows it re-keyed, and how many gave way."""

    moved: int
    gave_way: int


@dataclass(frozen=True, slots=True)
class _UniqueIds:
    """Every unique id an old circuit id's entities can be under, and the one built for its new id.

    Built from the pairs alone, with the builders the platforms create with,
    so it answers the same whatever the registry holds now: the closed
    families key by key, and an extension, whose key set is open, by its
    builder's scope prefix.
    """

    built: tuple[tuple[str, str, str], ...]
    """`(domain, old unique id, new unique id)` for every key of the closed families."""

    prefixes: Mapping[str, str]
    """Each extension scope prefix built for an old id, to the one built for its new id."""

    renamed: Mapping[str, str]
    """`built`'s old unique ids to their new ones."""

    @classmethod
    def of(cls, serial: str, mapping: Mapping[str, str]) -> _UniqueIds:
        """Build both answers for each pair of `mapping`."""
        built: list[tuple[str, str, str]] = []
        for old_id, new_id in mapping.items():
            for builders in (circuit_entity_unique_ids, pv_binding.inverter_card_unique_ids):
                old_ids, new_ids = builders(serial, old_id), builders(serial, new_id)
                built.extend(
                    (domain, old_unique_id, new_ids[domain, key])
                    for (domain, key), old_unique_id in old_ids.items()
                )
        prefixes = {
            extension.extension_unique_id_prefix(serial, old_scope): (
                extension.extension_unique_id_prefix(serial, new_scope)
            )
            for old_scope, new_scope in extension.rekeyed_scopes(mapping).items()
        }
        renamed = {old_unique_id: new_unique_id for _, old_unique_id, new_unique_id in built}
        return cls(tuple(built), prefixes, renamed)

    def new_unique_id(self, unique_id: str) -> str | None:
        """Return the unique id built for the new id of whatever `unique_id` was built for, or None."""
        renamed = self.renamed.get(unique_id)
        if renamed is not None:
            return renamed
        for old_prefix, new_prefix in self.prefixes.items():
            if unique_id.startswith(old_prefix):
                return new_prefix + unique_id[len(old_prefix) :]
        return None

    def in_entry(
        self, entity_registry: er.EntityRegistry, entry_id: str
    ) -> list[tuple[str, str, str]]:
        """`(domain, old unique id, new unique id)` for every built pair and every entry row under an old prefix."""
        pairs = list(self.built)
        for row in er.async_entries_for_config_entry(entity_registry, entry_id):
            for old_prefix, new_prefix in self.prefixes.items():
                if row.unique_id.startswith(old_prefix):
                    pairs.append(
                        (row.domain, row.unique_id, new_prefix + row.unique_id[len(old_prefix) :])
                    )
        return pairs


async def async_migrate_device_ids(
    hass: HomeAssistant, entry: SpanPanelConfigEntry, snapshot: SpanPanelSnapshot
) -> None:
    """Re-key everything this entry holds under a circuit's old id to its new one.

    `snapshot` is the setup's, from the first refresh. Devices go before
    entities, so a newer device's rows are gone when the originals take their
    ids; the stores follow, each re-keyed on disk before its owner loads it.
    One INFO line reports the counts when anything moved. A pairing two new
    ids refuse is warned of here, once a setup, and nothing moves.
    """
    pairing = pair_device_ids(snapshot)
    if pairing.conflict is not None:
        warn_of_trait_conflict(pairing.conflict)
    mapping = pairing.previous
    if not mapping:
        return
    serial = snapshot.serial_number
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    unique_ids = _UniqueIds.of(serial, mapping)

    _warn_on_renamed_pairs(device_registry, entry.entry_id, snapshot, mapping)
    devices = _rekey_devices(
        hass, device_registry, entity_registry, entry.entry_id, serial, mapping
    )
    entities = _rekey_entities(hass, entity_registry, entry.entry_id, unique_ids)

    stored = 0
    stored += await package_rating_manager.async_rekey_circuit_ids(hass, entry, mapping)
    stored += await current_monitor.async_rekey_circuit_ids(hass, entry, mapping)
    stored += await graph_horizon.async_rekey_circuit_ids(hass, entry, mapping)
    stored += await pv_binding.async_rekey_circuit_ids(hass, entry, mapping)
    stored += await curation.async_rekey_scopes(hass, entry, extension.rekeyed_scopes(mapping))
    panel = device_registry.async_get_device_by_identifier((DOMAIN, serial), entry.entry_id)
    if panel is not None:
        stored += await frontend.async_rekey_favorite_circuits(hass, panel.id, mapping)
    stored += await additions.async_rekey_unique_ids(hass, entry, unique_ids.new_unique_id)
    stored += extension.async_rekey_overflow_notice(hass, entry, mapping)
    issues = conversion_repairs.async_rekey_circuit_ids(hass, entry, serial, mapping)

    if devices.moved or entities.moved or stored or issues:
        _LOGGER.info(
            "%s: carried its circuits over to their new device ids: %d entities, %d devices, "
            "%d stored values and %d notices re-keyed; %d entities and %d devices an earlier "
            "release added under the new ids removed",
            entry.title,
            entities.moved,
            devices.moved,
            stored,
            issues,
            devices.entities_removed + entities.gave_way,
            devices.devices_removed,
        )


def holds_old_ids(
    hass: HomeAssistant, entry_id: str, serial: str, mapping: Mapping[str, str]
) -> bool:
    """Whether this entry still holds a row or a device under an old id of `mapping`.

    What the migration would move for those pairs in the registries: a
    circuit's own device, the card of an inverter it feeds, or any row whose
    unique id is built for the old id. A row counts wherever it sits, so an
    install from before circuit devices, whose rows are on the panel's
    device, counts too. Built with the migration's own look-ups, never by
    reading an id.

    Asked by the coordinator's guard (`device_id_migration.DeviceIdGuard`)
    when circuits a session was set up without arrive under new ids, bound to
    the entry by its setup (`entry_holds_old_ids`); a yes rebuilds the entry,
    whose setup migrates them.
    """
    device_registry = dr.async_get(hass)
    if any(
        circuit_device(device_registry, serial, old_id, config_entry_id=entry_id) is not None
        or _inverter_card(device_registry, entry_id, serial, old_id) is not None
        for old_id in mapping
    ):
        return True
    entity_registry = er.async_get(hass)
    return any(
        entity_id_in_entry(entity_registry, entry_id, domain, old_unique_id) is not None
        for domain, old_unique_id, _ in _UniqueIds.of(serial, mapping).in_entry(
            entity_registry, entry_id
        )
    )


def entry_holds_old_ids(hass: HomeAssistant, entry_id: str, serial: str) -> HoldsOldIds:
    """Return `holds_old_ids` bound to one entry, for the coordinator's guard.

    A closure rather than `functools.partial`, whose call takes any arguments,
    so the type checker holds what the guard calls to `HoldsOldIds`: one map
    of old ids to new ones, answering a boolean.
    """

    def holds(mapping: Mapping[str, str]) -> bool:
        return holds_old_ids(hass, entry_id, serial, mapping)

    return holds


def _warn_on_renamed_pairs(
    device_registry: dr.DeviceRegistry,
    entry_id: str,
    snapshot: SpanPanelSnapshot,
    mapping: Mapping[str, str],
) -> None:
    """Log each pair whose circuit device is named otherwise than its new circuit, as a support aid.

    Nothing gates on it: the pairing is the panel's trait instance, which a
    recommissioned panel reuses for another circuit, as it would on any
    firmware. The device's registry `name` is the panel's name for the circuit
    when it was last set up, never the user's own (`name_by_user`).

    Only a new circuit the panel names is compared. Names can lag the new
    tree: a rebuild the guard asks for can set up before they arrive, and the
    client waits for them only so long. A circuit without one would be
    compared by the placeholder it is given, its breaker positions, which
    says nothing about whether it is the same circuit.
    """
    serial = snapshot.serial_number
    for old_id, new_id in mapping.items():
        circuit = snapshot.circuits[new_id]
        if not circuit.name:
            continue
        device = circuit_device(device_registry, serial, old_id, config_entry_id=entry_id)
        if device is None or device.name is None:
            continue
        current = circuit_device_name(circuit, new_id)
        if device.name != current:
            _LOGGER.warning(
                "Circuit %s (%s) is carried over to %s, which the panel now names %s: the panel "
                "pairs them as the same circuit. If it is a different circuit, its entities "
                "and settings now describe it",
                old_id,
                device.name,
                new_id,
                current,
            )


def _rekey_devices(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    entry_id: str,
    serial: str,
    mapping: Mapping[str, str],
) -> _Moved:
    """Re-key each circuit's own device and each inverter card a circuit feeds, in place.

    Where a device already holds the new identifier and the original exists
    too, the newer one gives way: its entities and then the device are
    removed. Where only the newer one exists -- an install from before circuit
    devices that ran an earlier release across the change -- it is kept, and
    the conversion moves the re-keyed entities onto it.
    """
    moved = 0
    devices_removed = 0
    entities_removed = 0
    for old_id, new_id in mapping.items():
        new_identifier = build_circuit_device_identifier(serial, new_id)
        circuit = circuit_device(device_registry, serial, old_id, config_entry_id=entry_id)
        if circuit is not None:
            newer_circuit = circuit_device(
                device_registry, serial, new_id, config_entry_id=entry_id
            )
            if newer_circuit is not None:
                entities_removed += _remove_device(
                    hass, device_registry, entity_registry, entry_id, newer_circuit.id
                )
                devices_removed += 1
            device_registry.async_update_child_device(
                circuit.id, new_identifiers={(DOMAIN, new_identifier)}
            )
            moved += 1

        card = _inverter_card(device_registry, entry_id, serial, old_id)
        if card is not None:
            newer_card = _inverter_card(device_registry, entry_id, serial, new_id)
            if newer_card is not None:
                entities_removed += _remove_device(
                    hass, device_registry, entity_registry, entry_id, newer_card.id
                )
                devices_removed += 1
            device_registry.async_update_device(
                card.id,
                new_identifiers={(DOMAIN, pv_inverter_device_identifier(serial, new_id))},
            )
            moved += 1
    return _Moved(moved, devices_removed, entities_removed)


def _inverter_card(
    device_registry: dr.DeviceRegistry, entry_id: str, serial: str, circuit_id: str
) -> dr.DeviceEntry | None:
    """Return the card of the inverter `circuit_id` feeds, on a panel with several, or None."""
    return device_registry.async_get_device_by_identifier(
        (DOMAIN, pv_inverter_device_identifier(serial, circuit_id)), entry_id
    )


def _remove_device(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    entry_id: str,
    device_id: str,
) -> int:
    """Remove a device that gives way, its entities first; returns how many entities went.

    Its entities that have no original to take their ids come back at the
    platform add, on the re-keyed device, and Home Assistant restores their
    entity ids from the tombstones this leaves.
    """
    removed = 0
    for row in er.async_entries_for_device(
        entity_registry, device_id, include_disabled_entities=True
    ):
        if row.config_entry_id == entry_id:
            _give_way(hass, entity_registry, entry_id, row)
            removed += 1
    device_registry.async_remove_device(device_id)
    return removed


def _give_way(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, entry_id: str, row: er.RegistryEntry
) -> None:
    """Remove a row an earlier release added under a new id, and the notice raised about it.

    A breaker switch's area notice is keyed by the switch's unique id and
    describes that switch, so it gives way with the row: with the row gone, it
    describes nothing. Deleted in the same step, so no setup can stop between
    the two and leave it for the next, which would find no row to tell it by.
    """
    entity_registry.async_remove(row.entity_id)
    if row.domain == Platform.SWITCH:
        conversion_repairs.async_forget_breaker_switch(hass, entry_id, row.unique_id)


def _rekey_entities(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, entry_id: str, unique_ids: _UniqueIds
) -> _Rekeyed:
    """Re-key every entity row built for an old circuit id to the unique id built for its new one.

    A row already registered under the new id gives way: it was added by an
    earlier release after the change, and the original holds the history.
    """
    moved = 0
    gave_way = 0
    for domain, old_unique_id, new_unique_id in unique_ids.in_entry(entity_registry, entry_id):
        original = entity_id_in_entry(entity_registry, entry_id, domain, old_unique_id)
        if original is None:
            continue
        newer = entity_id_in_entry(entity_registry, entry_id, domain, new_unique_id)
        newer_row = entity_registry.async_get(newer) if newer is not None else None
        if newer_row is not None:
            _give_way(hass, entity_registry, entry_id, newer_row)
            gave_way += 1
        holder = entity_registry.async_get_entity_id(domain, DOMAIN, new_unique_id)
        if holder is not None:
            # Another entry's row. Keyed by the panel serial, so a second entry
            # for the same panel would have to exist; left as it is.
            _LOGGER.warning(
                "Not re-keying %s: its new unique id %s is held by %s of another entry",
                original,
                new_unique_id,
                holder,
            )
            continue
        entity_registry.async_update_entity(original, new_unique_id=new_unique_id)
        moved += 1
    return _Rekeyed(moved, gave_way)
