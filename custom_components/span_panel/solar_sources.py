"""The solar sources a panel has: its published inverters, and the circuits whose role is solar.

Keyed exactly as `pv_inverters` keys an inverter: by the circuit that feeds it,
or by its device id where none does. A circuit that says it feeds solar without
publishing an inverter device behind it is a source keyed by its own id -- the
key a `pv` device it later publishes would get, so nothing moves when one
appears. On a panel that publishes no roles the map is `pv_inverters` itself,
iteration order included.

A leaf: it imports only the standard library, `span_panel_api` and
`energy_orientation`, so the binding, the capability tokens and the topology
can all read one definition of a source.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from span_panel_api import SpanPanelSnapshot, SpanPVSnapshot

from .energy_orientation import circuit_is_generation


@dataclass(frozen=True, slots=True)
class SolarSource:
    """One source of solar the Solar cards read: a published inverter, or a circuit whose role is solar.

    `inverter` is the published inverter, or None for a circuit that publishes
    no inverter behind it, which therefore has no vendor, product or nameplate.
    """

    feed_circuit_id: str | None
    inverter: SpanPVSnapshot | None

    @property
    def connected(self) -> bool | None:
        """The inverter's link as its circuit records it; None for a role-only source."""
        return None if self.inverter is None else self.inverter.connected


def solar_sources(snapshot: SpanPanelSnapshot) -> Mapping[str, SolarSource]:
    """Every solar source, keyed as `pv_inverters` keys inverters: published inverters first, then solar-role circuits by id.

    Exactly `pv_inverters`, iteration order included, on a panel that publishes
    no roles.
    """
    sources: dict[str, SolarSource] = {
        key: SolarSource(feed_circuit_id=pv.feed_circuit_id, inverter=pv)
        for key, pv in snapshot.pv_inverters.items()
    }
    if snapshot.publishes_solar_roles:
        # A circuit already feeding a published inverter is that inverter's
        # source. Any other circuit oriented as generation -- its role is solar
        # (eBus connection/feeds-role SOLAR), or it declares it feeds a PV device
        # that is not published -- is a source of its own. Read through
        # `circuit_is_generation`, so a source and its readings agree on what it is.
        fed = {pv.feed_circuit_id for pv in snapshot.pv_inverters.values()}
        for circuit_id in sorted(snapshot.circuits):
            if circuit_id not in fed and circuit_is_generation(snapshot.circuits[circuit_id]):
                sources[circuit_id] = SolarSource(feed_circuit_id=circuit_id, inverter=None)
    return sources
