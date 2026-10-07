"""What the dashboard card draws for each solar inverter: its identity, and the reading that is its power.

Every inverter is represented alike. Its identity is on its device, and its
power is its feeding circuit's power; no entity duplicates a circuit reading.
The Solar device is the bound inverter's device, and also carries PV Power, the
site's total. The card cannot tell which circuit feeds which inverter and must
not guess, so the topology command says so for each PV device, as an *entity id*.
An entity id rather than a circuit id, because the favorites view merges
several panels' topologies: it re-keys and filters their circuits but copies
their sub-devices verbatim, so an entity id survives the merge and a circuit id
does not.

Structure comes from the setup snapshot and the binding resolved from it, the
same inputs the PV devices were built from, so a block always describes the
registered device graph. The Solar device's block follows `has_pv`, the test
that creates the device, so it exists whenever the device does. A structural
change reloads the entry, because every inverter key is a capability token.

On SPAN a hybrid's PV is an ordinary `pv` child of the panel: circuit-fed when
in-panel, identity-only when upstream, like span#269's inverters.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal, TypedDict

from .helpers import has_pv

if TYPE_CHECKING:
    from span_panel_api import SpanPanelSnapshot

    from .pv_binding import PvBinding

type SolarRole = Literal["site", "inverter"]


class SolarTopology(TypedDict):
    """The `solar` block on one PV sub-device in `span_panel/panel_topology`."""

    role: SolarRole
    vendor: str | None
    """As published. `None` where nothing is, unlike the registry's "Unknown" placeholder."""
    model: str | None
    feed_circuit_id: str | None
    power_entity_id: str | None
    """The inverter's own power reading: its feeding circuit's power entity, or `None` where no circuit feeds it."""
    site_power_entity_id: str | None
    """PV Power, the site's total, on the Solar device's block only."""


def solar_topology(
    inverter_key: str | None,
    binding: PvBinding,
    snapshot: SpanPanelSnapshot,
    circuit_power: Mapping[str, str],
    site_power_entity_id: str | None,
) -> SolarTopology | None:
    """Return the block for the Solar device (`inverter_key` None) or one inverter's own device, or None.

    None where there is nothing to describe: PV not commissioned, for the Solar
    device; or an inverter that is not published or has no card of its own.
    """
    if inverter_key is None:
        return _site(binding, snapshot, circuit_power, site_power_entity_id)
    inverter = snapshot.pv_inverters.get(inverter_key)
    if inverter is None or not binding.has_own_card(inverter_key):
        return None
    feed = inverter.feed_circuit_id
    return SolarTopology(
        role="inverter",
        vendor=inverter.vendor_name,
        model=inverter.model,
        feed_circuit_id=feed,
        power_entity_id=_power(feed, circuit_power),
        site_power_entity_id=None,
    )


def _site(
    binding: PvBinding,
    snapshot: SpanPanelSnapshot,
    circuit_power: Mapping[str, str],
    site_power_entity_id: str | None,
) -> SolarTopology | None:
    if not has_pv(snapshot):
        return None
    source = binding.source(snapshot)
    feed = _site_feed_circuit(binding, snapshot)
    return SolarTopology(
        role="site",
        vendor=source.vendor_name,
        model=source.model,
        feed_circuit_id=feed,
        power_entity_id=_power(feed, circuit_power),
        site_power_entity_id=site_power_entity_id,
    )


def _site_feed_circuit(binding: PvBinding, snapshot: SpanPanelSnapshot) -> str | None:
    """Return the circuit of the inverter the Solar device describes and that has no tile of its own.

    `legacy_key`, not `bound_key`: a bound key that also holds a card (a store
    restored from an older backup) already has its own tile, and drawing its
    circuit here too would show one reading twice. A bound inverter that is not
    published while its circuit is (a late record, with or without other
    inverters published, or an inverter gone from a breaker that stays) keeps
    that circuit: it is still the one the Solar device is bound to.
    """
    key = binding.legacy_key
    if key is None:
        return None
    inverter = snapshot.pv_inverters.get(key)
    if inverter is not None:
        return inverter.feed_circuit_id
    if key == binding.bound_key and key in snapshot.circuits:
        return key
    return None


def _power(feed: str | None, circuit_power: Mapping[str, str]) -> str | None:
    return circuit_power.get(feed) if feed is not None else None
