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

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from span_panel_api import SpanPVSnapshot

from .const import DOMAIN, PV_PANEL_LINK_KEY
from .entity_resolver import entity_id_in_entry
from .id_builder import build_binary_sensor_unique_id, build_pv_inverter_unique_id
from .sensor_definitions import PV_METADATA_SENSORS

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from span_panel_api import SpanPanelSnapshot

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION: Final = 1

_CARD_ENTITIES: Final[tuple[tuple[str, str], ...]] = (
    *(("sensor", description.key) for description in PV_METADATA_SENSORS),
    ("binary_sensor", PV_PANEL_LINK_KEY),
)
"""`(domain, description key)` of the entities an inverter's own card carries."""


type PvBindingMode = Literal["inverter", "unbound", "undecided"]
"""How a setup reads its inverters: bound to a circuit, unbound, or not yet decided."""


class StoredPvBinding(TypedDict):
    """The one shape this module writes: the bound circuit, or `None` for unbound."""

    circuit_id: str | None


@dataclass(frozen=True, slots=True)
class PvBinding:
    """How this setup reads its inverters. Held in `runtime_data` and nowhere else."""

    mode: PvBindingMode
    bound_key: str | None
    """The circuit the Solar card's PV entities are bound to."""

    legacy_key: str | None
    """The inverter the Solar card's PV entities describe at this setup. It gets no card of its own."""

    withheld: frozenset[str]
    """Device-id keys given no card while the bound circuit's record is pending."""

    solar_link: bool
    """Whether the Solar card has its link entity at this setup.

    Decided by what does not change with a reading: the entity is already
    registered, so it never vanishes, or every inverter the card reads publishes
    a link record. The second is what 2.1.1 asked of its one inverter. A link a
    circuit publishes reads OK, LOST or DEGRADED, so it is the record, never
    its reading, that decides; the reading is what the entity shows.
    """

    inverter_links: frozenset[str]
    """Keys whose own card has its link entity at this setup.

    `solar_link`'s rule applied per key, among the keys with a card of their
    own: the link is already registered, or the inverter's circuit publishes a
    link record. A registered link never vanishes; it reads unknown while its
    inverter reports nothing.
    """

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


UNDECIDED_PV_BINDING: Final = PvBinding(
    "undecided", None, None, frozenset(), solar_link=False, inverter_links=frozenset()
)
"""What setup resolves while no inverter has been seen: nothing bound, nothing withheld, nothing written.

Its link is kept only where the entity is already registered; see `resolve`.
"""


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


def _reads_link_records(snapshot: SpanPanelSnapshot, bound: str | None) -> bool:
    """Whether every inverter the Solar card reads publishes a link record.

    Bound, that is the bound inverter, which must be published to have one.
    Unbound, the card reads the lone inverter or the inverters together, so
    every inverter must have one: with any left unreported the aggregate is
    known only while another link is down, and the entity would come and go
    with a reading.
    """
    if bound is not None:
        pv = snapshot.pv_inverters.get(bound)
        return pv is not None and pv.connected is not None
    inverters = snapshot.pv_inverters.values()
    return bool(inverters) and all(pv.connected is not None for pv in inverters)


def _inverter_links(
    snapshot: SpanPanelSnapshot,
    legacy: str | None,
    withheld: frozenset[str],
    inverter_links_held: frozenset[str],
) -> frozenset[str]:
    """Return the carded keys whose link exists: registered already, or recorded by their circuit."""
    return frozenset(
        key
        for key, pv in snapshot.pv_inverters.items()
        if key != legacy
        and key not in withheld
        and (key in inverter_links_held or pv.connected is not None)
    )


def resolve(
    snapshot: SpanPanelSnapshot,
    record: StoredPvBinding | None,
    held: frozenset[str],
    *,
    link_held: bool,
    inverter_links_held: frozenset[str],
) -> tuple[PvBinding, StoredPvBinding | None]:
    """Decide this setup's identity, and the record to keep.

    `held` is the inverter keys that already have a card of their own,
    `link_held` whether the Solar card's link entity is already registered, and
    `inverter_links_held` the keys whose own link entity is. The
    record is written at first sight, binding only a lone circuit-fed inverter
    that holds no card; its only rewrite is refining an unbound record the same
    way. A key in `held` always keeps its card, and a held link is always kept:
    nothing here withdraws any of them.
    """
    inverters = snapshot.pv_inverters
    lone_fed = _lone_fed_circuit(snapshot)
    bindable = lone_fed if lone_fed is not None and lone_fed not in held else None
    if record is None:
        if not inverters:
            undecided = PvBinding(
                "undecided",
                None,
                None,
                frozenset(),
                solar_link=link_held,
                inverter_links=frozenset(),
            )
            return undecided, None
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
        solar_link = link_held or _reads_link_records(snapshot, bound)
        links = _inverter_links(snapshot, legacy, withheld, inverter_links_held)
        return PvBinding("inverter", bound, legacy, withheld, solar_link, links), record
    lone = next(iter(inverters)) if len(inverters) == 1 else None
    legacy = lone if lone is not None and lone not in held else None
    solar_link = link_held or _reads_link_records(snapshot, None)
    links = _inverter_links(snapshot, legacy, frozenset(), inverter_links_held)
    return PvBinding("unbound", None, legacy, frozenset(), solar_link, links), record


def store_for(hass: HomeAssistant, entry: ConfigEntry) -> Store[StoredPvBinding]:
    """Per entry. A `Store`, not config entry data: writing entry data during setup fires the update listener."""
    return Store(hass, _STORE_VERSION, f"{DOMAIN}.pv_binding.{entry.entry_id}")


def _registered_in(registry: er.EntityRegistry, entry_id: str, domain: str, unique_id: str) -> bool:
    """Whether `unique_id` is registered under `domain` in this entry; another entry's says nothing."""
    return entity_id_in_entry(registry, entry_id, domain, unique_id) is not None


def keys_holding_cards(
    registry: er.EntityRegistry, entry_id: str, serial: str, keys: Iterable[str]
) -> frozenset[str]:
    """Return the inverter keys among `keys` that already have an entity on a card of their own, in this entry.

    Built with `build_pv_inverter_unique_id`, as `SpanPVInverterSensor` and
    `SpanPVInverterBinarySensor` build theirs; scoped to `entry_id` like every
    registry lookup here.
    """

    def holds(key: str) -> bool:
        return any(
            _registered_in(
                registry,
                entry_id,
                domain,
                build_pv_inverter_unique_id(serial, key, description_key),
            )
            for domain, description_key in _CARD_ENTITIES
        )

    return frozenset(key for key in keys if holds(key))


def inverter_links_held(
    registry: er.EntityRegistry, entry_id: str, serial: str, keys: Iterable[str]
) -> frozenset[str]:
    """Return the keys among `keys` whose own link entity is already registered in this entry.

    Built with `build_pv_inverter_unique_id`, as `SpanPVInverterBinarySensor` builds its id.
    """
    return frozenset(
        key
        for key in keys
        if _registered_in(
            registry,
            entry_id,
            "binary_sensor",
            build_pv_inverter_unique_id(serial, key, PV_PANEL_LINK_KEY),
        )
    )


def solar_link_held(registry: er.EntityRegistry, entry_id: str, serial: str) -> bool:
    """Return whether the Solar card's link entity is already registered in this entry.

    Built with `build_binary_sensor_unique_id`, as `SpanPVSolarLinkBinarySensor`
    builds its id through the panel binary sensor it extends.
    """
    return _registered_in(
        registry,
        entry_id,
        "binary_sensor",
        build_binary_sensor_unique_id(serial, PV_PANEL_LINK_KEY),
    )


async def async_resolve_pv_binding(
    hass: HomeAssistant, entry: ConfigEntry, snapshot: SpanPanelSnapshot
) -> PvBinding:
    """Resolve this setup's identity, writing the record at first sight or at its one refinement."""
    store = store_for(hass, entry)
    record = read_record(await store.async_load())
    registry = er.async_get(hass)
    held = keys_holding_cards(
        registry, entry.entry_id, snapshot.serial_number, snapshot.pv_inverters
    )
    link_held = solar_link_held(registry, entry.entry_id, snapshot.serial_number)
    links_held = inverter_links_held(
        registry, entry.entry_id, snapshot.serial_number, snapshot.pv_inverters
    )
    identity, kept = resolve(
        snapshot, record, held, link_held=link_held, inverter_links_held=links_held
    )
    if kept is not None and kept != record:
        await store.async_save(kept)
        _LOGGER.info("Solar card's PV entities: %s", identity.mode)
    return identity


async def async_forget_pv_binding(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the record with the entry, as `curation.async_forget_curation` drops its own."""
    await store_for(hass, entry).async_remove()
