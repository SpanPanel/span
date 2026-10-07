"""Which inverter the Solar card's PV entities read: the one first seen, by its circuit.

Every install has PV entities on the panel's `{serial}_pv` Solar card --
`pv_vendor`, `pv_product`, `pv_nameplate_capacity`, `pv_panel_link` -- under
panel-scoped unique ids, beside the panel's total `pv_power`. Before firmware
r202639 a panel published one inverter, and only its feeding circuit carried a
connection record (SPAN's r202639 CHANGELOG), so that inverter was already read
by its circuit. The circuit is recorded once, at the first setup that sees an
inverter, and those entities read the inverter it feeds from then on. Every
other inverter gets a card of its own. Nothing is re-keyed or moved, and
nothing is matched by device id, serial or model.

Where the first setup sees one inverter no circuit feeds (an upstream PV, as
behind a Tesla Gateway in SpanPanel/span#269) or several at once, nothing says
which inverter the Solar card's entities meant. The record is unbound and they
read `snapshot.pv`: the lone inverter, or the inverters together. An unbound
record is refined to a circuit exactly once -- when the lone inverter the card
already reads, holding no card of its own, is published fed by that circuit --
because that only remembers what the card reads, before a second inverter
makes it unanswerable.

The record is needed because the moment it matters -- a second inverter
appearing -- is the moment neither the wire nor the registry can say which
inverter came first.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Final, Literal, TypedDict

from homeassistant.helpers.storage import Store
from span_panel_api import SpanPVSnapshot

from .const import DOMAIN, PV_PANEL_LINK_KEY
from .id_builder import build_pv_inverter_unique_id
from .sensor_definitions import PV_METADATA_SENSORS

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import entity_registry as er
    from span_panel_api import SpanPanelSnapshot

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION: Final = 1

_CARD_ENTITIES: Final[tuple[tuple[str, str], ...]] = (
    *(("sensor", description.key) for description in PV_METADATA_SENSORS),
    ("binary_sensor", PV_PANEL_LINK_KEY),
)
"""`(domain, description key)` of the entities an inverter's own card carries."""


class StoredPvBinding(TypedDict):
    """The one shape this module writes: the bound circuit, or `None` for unbound."""

    circuit_id: str | None


@dataclass(frozen=True, slots=True)
class PvBinding:
    """How this setup reads its inverters. Held in `runtime_data` and nowhere else."""

    mode: Literal["inverter", "unbound", "undecided"]
    bound_key: str | None
    """The circuit the Solar card's PV entities are bound to."""

    legacy_key: str | None
    """The inverter the Solar card's PV entities describe at this setup. It gets no card of its own."""

    withheld: frozenset[str]
    """Device-id keys given no card while the bound circuit's record is pending."""

    def has_own_card(self, key: str) -> bool:
        """Whether inverter `key` gets a card and entities of its own at this setup."""
        return key != self.legacy_key and key not in self.withheld

    def source(self, snapshot: SpanPanelSnapshot) -> SpanPVSnapshot:
        """Return what the Solar card's PV entities read: the bound inverter, else `snapshot.pv`.

        The empty snapshot while the bound inverter is not published, as a
        departed SPAN Drive reads empty.
        """
        if self.bound_key is None:
            return snapshot.pv
        return snapshot.pv_inverters.get(self.bound_key, SpanPVSnapshot())


UNDECIDED_PV_BINDING: Final = PvBinding("undecided", None, None, frozenset())
"""What setup resolves while no inverter has been seen: nothing bound, nothing withheld, nothing written."""


def read_record(stored: object) -> StoredPvBinding | None:
    """Return the stored record, or `None` where the file is absent or not what this module wrote.

    This runs inside `async_setup_entry`, where an exception would leave the
    entry in SETUP_ERROR (see `additions._load`). An unreadable record is decided
    again from the current snapshot.
    """
    if not isinstance(stored, dict) or "circuit_id" not in stored:
        return None
    circuit_id: object = stored["circuit_id"]
    if circuit_id is None or isinstance(circuit_id, str):
        return StoredPvBinding(circuit_id=circuit_id)
    return None


def _lone_fed_circuit(snapshot: SpanPanelSnapshot) -> str | None:
    """Return the lone inverter's circuit, where exactly one inverter is published and a circuit feeds it."""
    if len(snapshot.pv_inverters) != 1:
        return None
    key, pv = next(iter(snapshot.pv_inverters.items()))
    return key if pv.feed_circuit_id is not None else None


def resolve(
    snapshot: SpanPanelSnapshot, record: StoredPvBinding | None, held: frozenset[str]
) -> tuple[PvBinding, StoredPvBinding | None]:
    """Decide this setup's identity, and the record to keep.

    `held` is the inverter keys that already have a card of their own. The
    record is written at first sight, binding only a lone circuit-fed inverter
    that holds no card; its only rewrite is refining an unbound record the same
    way. A key in `held` always keeps its card: nothing here withdraws one.
    """
    inverters = snapshot.pv_inverters
    lone_fed = _lone_fed_circuit(snapshot)
    bindable = lone_fed if lone_fed is not None and lone_fed not in held else None
    if record is None:
        if not inverters:
            return UNDECIDED_PV_BINDING, None
        record = StoredPvBinding(circuit_id=bindable)
    elif record["circuit_id"] is None and bindable is not None:
        record = StoredPvBinding(circuit_id=bindable)
    bound = record["circuit_id"]
    if bound is not None:
        pending = bound not in inverters and bound in snapshot.circuits
        withheld = frozenset(
            key
            for key, pv in inverters.items()
            if pending and pv.feed_circuit_id is None and key not in held
        )
        # A bound key that somehow holds a card (a store restored from an older backup) keeps it.
        legacy = None if bound in held else bound
        return PvBinding("inverter", bound, legacy, withheld), record
    lone = next(iter(inverters)) if len(inverters) == 1 else None
    legacy = lone if lone is not None and lone not in held else None
    return PvBinding("unbound", None, legacy, frozenset()), record


def store_for(hass: HomeAssistant, entry: ConfigEntry) -> Store[StoredPvBinding]:
    """Per entry. A `Store`, not config entry data: writing entry data during setup fires the update listener."""
    return Store(hass, _STORE_VERSION, f"{DOMAIN}.pv_binding.{entry.entry_id}")


def keys_holding_cards(
    registry: er.EntityRegistry, entry_id: str, serial: str, keys: Iterable[str]
) -> frozenset[str]:
    """Return the inverter keys among `keys` that already have an entity on a card of their own, in this entry.

    Built with `build_pv_inverter_unique_id`, as `SpanPVInverterSensor` and
    `SpanPVInverterBinarySensor` build theirs; scoped to `entry_id` like every
    registry lookup here.
    """

    def holds(key: str) -> bool:
        for domain, description_key in _CARD_ENTITIES:
            entity_id = registry.async_get_entity_id(
                domain, DOMAIN, build_pv_inverter_unique_id(serial, key, description_key)
            )
            entry = registry.async_get(entity_id) if entity_id is not None else None
            if entry is not None and entry.config_entry_id == entry_id:
                return True
        return False

    return frozenset(key for key in keys if holds(key))
