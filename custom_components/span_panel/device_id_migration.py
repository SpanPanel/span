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
other readers ask: the coordinator, whether a snapshot names by new ids the
circuits a running session holds under old ones (`migration_pending`), and a
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

from collections.abc import Mapping
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


def previous_device_ids(snapshot: SpanPanelSnapshot) -> Mapping[str, str]:
    """Map each old-form circuit id to the new-form id of the same trait instance.

    Built from the snapshot alone. Two new ids claiming one trait instance
    refuse the whole mapping, with a WARNING: SPAN says it cannot happen, and a
    guess between them would re-key one circuit's history onto another.
    """
    mapping: dict[str, str] = {}
    for circuit_id in snapshot.circuits:
        match = _NEW_FORM.fullmatch(circuit_id)
        if match is None:
            continue
        old_id = f"{_OLD_FAMILY[match['trait']]}-{match['n']}"
        if old_id in mapping:
            _LOGGER.warning(
                "Device ids %s and %s share trait instance %s; not migrating",
                mapping[old_id],
                circuit_id,
                old_id,
            )
            return _NO_PAIRS
        mapping[old_id] = circuit_id
    return MappingProxyType(mapping)


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


def migration_pending(session_circuit_ids: frozenset[str], snapshot: SpanPanelSnapshot) -> bool:
    """Whether the snapshot calls, by new ids, circuits this session is keyed by under old ones.

    One set difference per snapshot: the pairing is worked out only when a
    circuit the session holds is missing. A circuit that has left a panel whose
    ids are already new, or opaque, is missing with no old-form counterpart, so
    the answer is no.
    """
    missing = session_circuit_ids - snapshot.circuits.keys()
    return bool(missing) and not missing.isdisjoint(previous_device_ids(snapshot))


def current_circuit_id(snapshot: SpanPanelSnapshot, circuit_id: str, *, service: str) -> str:
    """Return the id the panel calls a circuit now, for an id a service call named it by.

    An old id the snapshot pairs with a new one answers the new one, with one
    WARNING naming `service` and both ids, so the automation that sent it can be
    corrected. Anything else, a live id included, answers itself.
    """
    if circuit_id in snapshot.circuits:
        return circuit_id
    current = previous_device_ids(snapshot).get(circuit_id)
    if current is None:
        return circuit_id
    _LOGGER.warning(
        "%s named circuit %s by the device id it had before a panel firmware update; applied "
        "to %s, its id now. Name the circuit's entity instead, which keeps working across a "
        "change of device id",
        service,
        circuit_id,
        current,
    )
    return current
