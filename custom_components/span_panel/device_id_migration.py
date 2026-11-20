"""Which circuit id a panel calls a circuit now that it called something else before.

Firmware r202645 gives a panel's branch circuits and remote CTs new device ids.
A branch circuit that was `circuit-<n>` becomes `{prefix}-1-16-<n>`, and a
remote CT that was `remote-ct-<n>` becomes `{prefix}-1-42-<n>`. `<n>` is the
same trait instance on both releases, which is the one thing that pairs an old
id with its new one. A panel whose ids are opaque already, such as a 32-digit
hexadecimal id, publishes no such suffix and pairs nothing.

Everything this integration keys by a circuit's id -- unique ids, device
identifiers, stored settings -- is re-keyed once, at setup, by
`device_id_rekey.async_migrate_device_ids`. This module is what that and two
other readers ask: the coordinator, whether a snapshot carries by new ids
circuits the entry still holds under old ones (`DeviceIdGuard`), and a
service, which circuit an automation means when it names one by its old id
(`current_circuit_id`).

**The only reader of the parts of a circuit id.** Every other module treats an
id as opaque, and so does this one once the pairing is made: the map runs old
to new and is consumed only to look up or rebuild stored values. Nothing reads
the parts of a new id.

**A leaf, split from `device_id_rekey` on purpose.** The coordinator asks this
module on every snapshot and the service resolver on every call, while
`device_id_rekey` imports every store's owner, several of which import the
coordinator. Merged, the two would be an import cycle. Nothing here imports
anything from this package, and nothing may.

**For as long as `migrations.py` keeps its oldest step,** and reviewed with it.
The re-key, the coordinator's guard and the old ids services accept all go with
this module; an install that has not set up on r202645 by then is one no
migration reaches either.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Set
from dataclasses import dataclass
import logging
import re
from types import MappingProxyType
from typing import Final

from span_panel_api import SpanPanelSnapshot

_LOGGER = logging.getLogger(__name__)

_NEW_FORM: Final = re.compile(r".+-1-(?P<trait>16|42)-(?P<n>[0-9]+)")
"""A new circuit id, matched whole and anchored on its trait suffix alone.

The prefix is not checked. Every capture shows it to be the root device's id
followed by sixteen hexadecimal digits, but the scope is this panel's already --
a snapshot holds only the root's descendants -- and checking it would add two
ways to fail silently: the serial in it is read from the panel's own state, and
a serial holding `-` makes the split ambiguous.
"""

_OLD_FAMILY: Final = MappingProxyType({"16": "circuit", "42": "remote-ct"})
"""The old id's family for each trait: a branch circuit's, and a remote CT's."""

_NO_PAIRS: Final[Mapping[str, str]] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class TraitConflict:
    """Two new ids claiming one trait instance, which refuses the whole pairing."""

    trait_instance: str
    """The old id both would take, such as `circuit-5`."""

    first: str
    second: str


@dataclass(frozen=True, slots=True)
class DeviceIdPairing:
    """What a snapshot says each old-form circuit id is called now, or why it says nothing."""

    previous: Mapping[str, str]
    """Each old-form id to the new-form id of the same trait instance; empty when refused."""

    conflict: TraitConflict | None = None
    """The trait instance two new ids claim, when that refused the pairing."""


def pair_device_ids(snapshot: SpanPanelSnapshot) -> DeviceIdPairing:
    """Pair each old-form circuit id with the new-form id of the same trait instance.

    Built from the snapshot alone, and pure: it logs nothing. Two new ids
    claiming one trait instance refuse the whole pairing -- SPAN says it cannot
    happen, and a guess between them would re-key one circuit's history onto
    another -- and the refusal comes back as `conflict`, for the caller to warn
    of once (`warn_of_trait_conflict`), since some callers ask on every
    snapshot.
    """
    mapping: dict[str, str] = {}
    for circuit_id in snapshot.circuits:
        match = _NEW_FORM.fullmatch(circuit_id)
        if match is None:
            continue
        old_id = f"{_OLD_FAMILY[match['trait']]}-{match['n']}"
        if old_id in mapping:
            return DeviceIdPairing(_NO_PAIRS, TraitConflict(old_id, mapping[old_id], circuit_id))
        mapping[old_id] = circuit_id
    return DeviceIdPairing(MappingProxyType(mapping))


def previous_device_ids(snapshot: SpanPanelSnapshot) -> Mapping[str, str]:
    """Map each old-form circuit id to the new-form id of the same trait instance; empty when refused."""
    return pair_device_ids(snapshot).previous


def warn_of_trait_conflict(conflict: TraitConflict) -> None:
    """Log, at WARNING, that two new ids claim one trait instance, so nothing is migrated."""
    _LOGGER.warning(
        "Device ids %s and %s share trait instance %s; not migrating",
        conflict.first,
        conflict.second,
        conflict.trait_instance,
    )


def rekeyed[ValueT](
    values: Mapping[str, ValueT], mapping: Mapping[str, str]
) -> tuple[dict[str, ValueT], int]:
    """Return `values` with each key `mapping` names moved to its new id, and how many moved.

    The rule every store re-keys by: a value already held under the new id wins
    and the old one is dropped. Only a person writes a circuit-keyed setting,
    so one under the new id records something done after the panel changed its
    ids. Keys the mapping does not name keep their place; moved ones follow.
    """
    kept = {key: value for key, value in values.items() if key not in mapping}
    moved = 0
    for key, value in values.items():
        current = mapping.get(key)
        if current is None:
            continue
        moved += 1
        kept.setdefault(current, value)
    return kept, moved


def migration_pending(missing: Set[str], pairing: DeviceIdPairing) -> bool:
    """Whether a snapshot calls, by new ids, circuits a session is keyed by under old ones.

    `missing` is the session's circuits the snapshot lacks, and `pairing` the
    snapshot's. A circuit that has left a panel whose ids are already new, or
    opaque, is missing with no old-form counterpart, so the answer is no; so
    it is for a refused pairing.
    """
    return not missing.isdisjoint(pairing.previous)


type HoldsOldIds = Callable[[Mapping[str, str]], bool]
"""Whether an entry still holds rows or devices under an old id of these pairs, old to new.

`device_id_rekey.holds_old_ids`, bound to one entry by its setup. A callable
rather than an import, because that module imports the coordinator's
dependents and this one may import nothing of this package.
"""


class DeviceIdGuard:
    """What a running session asks of every snapshot: does it carry circuits the entry holds under old ids.

    A snapshot does in two ways, and either drops it and rebuilds the entry,
    whose setup migrates:

    - **it lacks circuits the session is keyed by and pairs them with new
      ids** -- the panel updated while the session ran (`migration_pending`);
    - **it brings circuits the session does not know whose old ids the entry
      still holds rows or devices under** -- the session was set up on a tree
      missing them, and they have now arrived. Rows count wherever they sit,
      so an install from before circuit devices, whose rows are on the
      panel's device, counts too.

    Neither fires on a panel whose ids are opaque, which pairs nothing; on a
    circuit that leaves, which arrives nowhere; or on a downgrade, whose old
    ids pair nothing.

    **Judged once per set of circuits.** A snapshot with the circuits the last
    one judged had answers what that one did, so a settled panel costs one set
    comparison per snapshot: no pairing, no registry look-up. Only a change in
    which circuits the panel publishes is judged, and only a pair whose new id
    the session does not know is looked up in the registries. A cached answer
    stays right because nothing registers state under an old id once a
    session is keyed by new ones.
    """

    def __init__(self, session: Set[str], holds_old_ids: HoldsOldIds) -> None:
        """Arm the guard with the circuit ids the session is keyed by and the entry's registry look-up."""
        self._session = frozenset(session)
        self._holds_old_ids = holds_old_ids
        self._judged = self._session
        self._pending = False
        self._conflict_warned = False

    def migration_pending(self, snapshot: SpanPanelSnapshot) -> bool:
        """Whether this snapshot carries circuits the entry holds under old ids, so it must not be read.

        A pairing two new ids refuse answers no, and is warned of once a
        session rather than at every set of circuits judged.
        """
        circuits = snapshot.circuits.keys()
        if circuits == self._judged:
            return self._pending
        self._judged = frozenset(circuits)
        pairing = pair_device_ids(snapshot)
        if pairing.conflict is not None and not self._conflict_warned:
            self._conflict_warned = True
            warn_of_trait_conflict(pairing.conflict)
        arrived = {
            old_id: new_id
            for old_id, new_id in pairing.previous.items()
            if new_id not in self._session
        }
        self._pending = migration_pending(self._session - self._judged, pairing) or (
            bool(arrived) and self._holds_old_ids(arrived)
        )
        return self._pending


def current_circuit_id(
    snapshot: SpanPanelSnapshot, circuit_id: str, *, service: str, takes_entity: bool
) -> str:
    """Return the id the panel calls a circuit now, for an id a service call named it by.

    An old id the snapshot pairs with a new one answers the new one, with one
    WARNING naming `service` and both ids, so the automation that sent it can be
    corrected. Anything else, a live id included, answers itself.

    The WARNING says what to send instead. A service that also takes the
    circuit's entity (`takes_entity`) is told to name the entity, which keeps
    working across a change of device id; one that takes only an id is told
    the new id, since an entity there would be stored as an id no circuit has.
    """
    if circuit_id in snapshot.circuits:
        return circuit_id
    current = previous_device_ids(snapshot).get(circuit_id)
    if current is None:
        return circuit_id
    _LOGGER.warning(
        "%s named circuit %s by the device id it had before a panel firmware update; applied "
        "to %s, its id now. %s",
        service,
        circuit_id,
        current,
        (
            "Name the circuit's entity instead, which keeps working across a change of device id"
            if takes_entity
            else f"Name it {current} instead: this action takes a circuit id, not an entity"
        ),
    )
    return current
