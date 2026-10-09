"""Solar sources: the published inverters, then the circuits whose role is solar, bound by one rule.

On a panel that publishes no roles the sources are exactly `pv_inverters`, so
every MAIN 32 binding, entity and topology block is what it was. Where a panel
says where its solar is, a circuit whose role is solar and that feeds no
published inverter is a source keyed by its own id, bound at first sight as a
lone circuit-fed inverter is, and its block in the topology says so. A battery
with no solar makes no Solar device, and PV power reported with no source to
attribute it to is a diagnostics row.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Final

from homeassistant.core import HomeAssistant
import pytest
from span_panel_api import FeedsRole, SpanCircuitSnapshot, SpanPanelSnapshot, SpanPVSnapshot

from custom_components.span_panel.diagnostics import async_get_config_entry_diagnostics
from custom_components.span_panel.helpers import has_pv
from custom_components.span_panel.pv_binding import (
    StoredPvBinding,
    resolve,
    resolve_sources,
)
from custom_components.span_panel.solar_sources import SolarSource, solar_sources
from custom_components.span_panel.solar_topology import solar_topology

from .captures_replay import CAPTURES, Capture, snapshot
from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory
from .test_diagnostics import _reference_entry

ROOF: Final = "circuit-roof"
GARAGE: Final = "circuit-garage"
KITCHEN: Final = "circuit-kitchen"


def _circuit(
    circuit_id: str, tabs: list[int], *, role: FeedsRole | None = None, device_type: str = "circuit"
) -> SpanCircuitSnapshot:
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id=circuit_id, name=circuit_id, tabs=tabs, device_type=device_type
    )
    return replace(circuit, feeds_role=role)


def _roles_panel(
    *circuits: SpanCircuitSnapshot, power_flow_pv: float | None = None
) -> SpanPanelSnapshot:
    panel = SpanPanelSnapshotFactory.create(
        circuits={circuit.circuit_id: circuit for circuit in circuits},
        power_flow_pv=power_flow_pv,
    )
    return replace(panel, publishes_solar_roles=True)


@pytest.mark.parametrize("captured", CAPTURES, ids=[c.stem for c in CAPTURES])
def test_main32_sources_equal_pv_inverters(captured: Capture) -> None:
    """Identical mapping, iteration order included, so the binding is what it was."""
    replayed = snapshot(captured.tree())

    assert replayed.publishes_solar_roles is False
    sources = solar_sources(replayed)
    assert list(sources) == list(replayed.pv_inverters)
    assert all(
        sources[key] == SolarSource(feed_circuit_id=pv.feed_circuit_id, inverter=pv)
        for key, pv in replayed.pv_inverters.items()
    )


@pytest.mark.spec_only
def test_one_solar_role_circuit_binds_at_first_sight() -> None:
    panel = _roles_panel(_circuit(ROOF, [2, 4], role="SOLAR"), _circuit(KITCHEN, [1]))

    identity, record = resolve(
        panel, None, frozenset(), link_held=False, inverter_links_held=frozenset()
    )

    assert list(solar_sources(panel)) == [ROOF]
    assert record == StoredPvBinding(circuit_id=ROOF)
    assert (identity.mode, identity.bound_key, identity.legacy_key) == ("inverter", ROOF, ROOF)
    assert has_pv(panel) is True


@pytest.mark.spec_only
def test_a_second_solar_role_circuit_gets_its_own_card() -> None:
    panel = _roles_panel(
        _circuit(ROOF, [2, 4], role="SOLAR"), _circuit(GARAGE, [6, 8], role="SOLAR")
    )

    identity, record = resolve(
        panel,
        StoredPvBinding(circuit_id=ROOF),
        frozenset(),
        link_held=False,
        inverter_links_held=frozenset(),
    )

    assert record == StoredPvBinding(circuit_id=ROOF)
    assert identity.legacy_key == ROOF
    assert identity.has_own_card(GARAGE) is True
    assert identity.has_own_card(ROOF) is False


@pytest.mark.spec_only
def test_the_secondary_signal_alone_counts() -> None:
    """A circuit that declares it feeds a PV device the panel does not publish is a source."""
    panel = _roles_panel(
        _circuit(ROOF, [2, 4], device_type="pv"), _circuit(KITCHEN, [1], role="LOADS")
    )

    assert list(solar_sources(panel)) == [ROOF]
    assert solar_sources(panel)[ROOF] == SolarSource(feed_circuit_id=ROOF, inverter=None)


def test_resolve_is_order_independent() -> None:
    sources: Mapping[str, SolarSource] = {
        "upstream": SolarSource(feed_circuit_id=None, inverter=SpanPVSnapshot()),
        ROOF: SolarSource(feed_circuit_id=ROOF, inverter=None),
        GARAGE: SolarSource(feed_circuit_id=GARAGE, inverter=None),
    }
    reversed_sources = dict(reversed(list(sources.items())))
    circuits = {ROOF, GARAGE, KITCHEN}

    for record in (None, StoredPvBinding(circuit_id=None), StoredPvBinding(circuit_id=GARAGE)):
        for held in (frozenset(), frozenset({ROOF})):
            forward = resolve_sources(
                sources, circuits, record, held, link_held=False, inverter_links_held=frozenset()
            )
            backward = resolve_sources(
                reversed_sources,
                circuits,
                record,
                held,
                link_held=False,
                inverter_links_held=frozenset(),
            )
            assert forward == backward, (record, held)


@pytest.mark.spec_only
def test_a_solar_circuit_later_named_by_a_pv_device_keeps_its_key() -> None:
    """The inverter it later publishes is keyed by the same circuit, so nothing moves and nothing is new."""
    record = StoredPvBinding(circuit_id=ROOF)
    later = replace(
        _roles_panel(_circuit(ROOF, [2, 4], role="SOLAR", device_type="pv")),
        pv_inverters={ROOF: SpanPVSnapshot(feed_circuit_id=ROOF, vendor_name="Example")},
    )

    identity, kept = resolve(
        later, record, frozenset(), link_held=False, inverter_links_held=frozenset()
    )

    assert list(solar_sources(later)) == [ROOF]
    assert kept == record
    assert (identity.bound_key, identity.legacy_key) == (ROOF, ROOF)


def test_a_battery_without_solar_creates_no_solar_device() -> None:
    """Power-flows PV at 0.0 beside a battery is not evidence of solar where the panel publishes roles."""
    panel = _roles_panel(_circuit(KITCHEN, [1], role="LOADS"), power_flow_pv=0.0)

    assert has_pv(panel) is False
    assert has_pv(replace(panel, publishes_solar_roles=False)) is True, "the flat rule is unchanged"


@pytest.mark.parametrize(("power", "row"), [(250.0, 250.0), (0.0, None), (None, None)])
async def test_the_tripwire_row(
    hass: HomeAssistant, power: float | None, row: float | None
) -> None:
    panel = _roles_panel(_circuit(KITCHEN, [1], role="LOADS"), power_flow_pv=power)

    result = await async_get_config_entry_diagnostics(hass, _reference_entry(panel))

    assert result["pv_power_without_source_w"] == row


@pytest.mark.spec_only
def test_the_solar_device_block_of_a_role_source_says_so() -> None:
    panel = _roles_panel(_circuit(ROOF, [2, 4], role="SOLAR"))
    identity, _ = resolve(
        panel, None, frozenset(), link_held=False, inverter_links_held=frozenset()
    )

    block = solar_topology(
        None, identity, panel, panel, {ROOF: "sensor.roof_power"}, "sensor.pv_power"
    )

    assert block is not None
    assert block["identity"] == "role"
    assert (block["vendor"], block["model"]) == (None, None)
    assert block["feed_circuit_id"] == ROOF
    assert block["power_entity_id"] == "sensor.roof_power"
