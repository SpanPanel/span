"""The topology command's `solar` block: which inverter a PV device shows and what its power is."""

from __future__ import annotations

from typing import Final

from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel.helpers import has_pv
from custom_components.span_panel.pv_binding import PvBinding
from custom_components.span_panel.solar_topology import SolarTopology, solar_topology
from custom_components.span_panel.util import PvDeviceRef, pv_device_ref

from .adapter_fixtures import schema_one_snapshot, schema_one_tree
from .factories import SpanPanelSnapshotFactory, pv_binding_for
from .test_pv_binding import NEW_CIRCUIT, _gateway_tree, _unfed_tree
from .test_pv_device import PV_DEVICE, SOLAR_CIRCUIT
from .test_pv_inverters import SECOND_SOLAR_CIRCUIT, _tree

PANEL = "sp3-solar-001"


def test_the_solar_device_is_the_panels_pv_identifier() -> None:
    assert pv_device_ref(f"{PANEL}_pv", PANEL) == PvDeviceRef(None)


def test_an_inverter_device_carries_its_key() -> None:
    assert pv_device_ref(f"{PANEL}_pv_5be1d2c3a4f5061728394a5b6c7d8e9f", PANEL) == PvDeviceRef(
        "5be1d2c3a4f5061728394a5b6c7d8e9f"
    )
    assert pv_device_ref(f"{PANEL}_pv_panel-se7600h-us-1", PANEL) == PvDeviceRef(
        "panel-se7600h-us-1"
    )


def test_nothing_else_is_a_pv_device() -> None:
    assert pv_device_ref(f"{PANEL}_bess", PANEL) is None
    assert pv_device_ref(f"{PANEL}_evse_node_pv_1", PANEL) is None
    assert pv_device_ref(f"{PANEL}_adopted_pv", PANEL) is None
    assert pv_device_ref(f"{PANEL}_pv_", PANEL) is None
    assert pv_device_ref("other-panel_pv", PANEL) is None


C: Final = SOLAR_CIRCUIT
C2: Final = SECOND_SOLAR_CIRCUIT
POWER: Final = {
    C: "sensor.span_panel_commissioned_pv_system_power",
    C2: "sensor.span_panel_garage_solar_power",
}
SITE: Final = "sensor.span_panel_pv_power"


def _bound(
    key: str, *, legacy: str | None = None, withheld: frozenset[str] = frozenset()
) -> PvBinding:
    return PvBinding(
        "inverter", key, legacy, withheld, solar_link=False, inverter_links=frozenset()
    )


def _site(binding: PvBinding, snapshot: SpanPanelSnapshot) -> SolarTopology | None:
    return solar_topology(None, binding, snapshot, POWER, SITE)


def _no_inverter_published() -> SpanPanelSnapshot:
    """PV commissioned -- the capture publishes `power-flows/pv` -- with no inverter published."""
    tree = _unfed_tree()
    tree.pop(PV_DEVICE)
    snapshot = schema_one_snapshot(tree)
    assert has_pv(snapshot) and not snapshot.pv_inverters
    return snapshot


def test_one_bound_inverter_puts_its_circuit_on_the_site_tile() -> None:
    snapshot = schema_one_snapshot()
    site = _site(pv_binding_for(snapshot), snapshot)

    assert site == SolarTopology(
        role="site",
        vendor=snapshot.pv_inverters[C].vendor_name,
        model=snapshot.pv_inverters[C].model,
        feed_circuit_id=C,
        power_entity_id=POWER[C],
        site_power_entity_id=SITE,
    )


def test_each_other_inverter_gets_its_own_circuit() -> None:
    snapshot = schema_one_snapshot(_tree())
    binding = _bound(C, legacy=C)

    site = _site(binding, snapshot)
    second = solar_topology(C2, binding, snapshot, POWER, SITE)

    assert site is not None and site["power_entity_id"] == POWER[C]
    assert second == SolarTopology(
        role="inverter",
        vendor=snapshot.pv_inverters[C2].vendor_name,
        model=snapshot.pv_inverters[C2].model,
        feed_circuit_id=C2,
        power_entity_id=POWER[C2],
        site_power_entity_id=None,
    )
    assert solar_topology(C, binding, snapshot, POWER, SITE) is None


def test_unbound_over_several_leaves_the_site_tile_on_the_site_total() -> None:
    snapshot = schema_one_snapshot(_tree())
    binding = pv_binding_for(snapshot)
    assert binding.mode == "unbound"

    site = _site(binding, snapshot)

    assert site is not None
    assert site["feed_circuit_id"] is None
    assert site["power_entity_id"] is None
    assert site["site_power_entity_id"] == SITE
    first = solar_topology(C, binding, snapshot, POWER, SITE)
    assert first is not None and first["power_entity_id"] == POWER[C]


def test_an_inverter_no_circuit_feeds_has_no_individual_reading() -> None:
    snapshot = schema_one_snapshot(
        _gateway_tree(
            ("panel-se7600h-us-1", "SE7600H-US", "11680"),
            ("panel-use7600h-us-2", "USE7600H-US", "11680"),
        )
    )
    binding = pv_binding_for(snapshot)

    site = _site(binding, snapshot)
    inverter = solar_topology("panel-use7600h-us-2", binding, snapshot, POWER, SITE)

    assert site is not None and site["vendor"] == "SolarEdge" and site["model"] is None
    assert site["power_entity_id"] is None
    assert inverter is not None
    assert inverter["feed_circuit_id"] is None and inverter["power_entity_id"] is None
    assert inverter["model"] == "USE7600H-US"


def test_a_pending_record_keeps_the_bound_circuit_on_the_site_tile() -> None:
    snapshot = schema_one_snapshot(_unfed_tree())
    binding = _bound(C, legacy=C, withheld=frozenset({PV_DEVICE}))

    site = _site(binding, snapshot)

    assert site is not None
    assert site["feed_circuit_id"] == C and site["power_entity_id"] == POWER[C]
    assert site["vendor"] is None
    assert solar_topology(PV_DEVICE, binding, snapshot, POWER, SITE) is None


def test_a_bound_circuit_that_is_gone_leaves_the_site_total() -> None:
    tree = schema_one_tree()
    tree[NEW_CIRCUIT] = tree.pop(SOLAR_CIRCUIT)
    snapshot = schema_one_snapshot(tree)

    site = _site(_bound(C, legacy=C), snapshot)

    assert site is not None and site["feed_circuit_id"] is None and site["power_entity_id"] is None


def test_a_bound_key_holding_a_card_leaves_the_site_tile_on_the_site_total() -> None:
    """A restored store: the bound inverter has its own tile, so the site tile must not draw its circuit too."""
    snapshot = schema_one_snapshot(_tree())
    binding = _bound(C, legacy=None)

    site = _site(binding, snapshot)
    own = solar_topology(C, binding, snapshot, POWER, SITE)

    assert site is not None and site["power_entity_id"] is None
    assert own is not None and own["power_entity_id"] == POWER[C]


def test_pv_commissioned_with_no_inverter_published_heads_the_site_tile_with_the_total() -> None:
    """The Solar device exists whenever PV is commissioned, so its block does too (review I-1)."""
    snapshot = _no_inverter_published()

    assert _site(pv_binding_for(snapshot), snapshot) == SolarTopology(
        role="site",
        vendor=None,
        model=None,
        feed_circuit_id=None,
        power_entity_id=None,
        site_power_entity_id=SITE,
    )


def test_a_pending_record_with_no_inverter_published_keeps_the_bound_circuit() -> None:
    snapshot = _no_inverter_published()

    site = _site(_bound(C, legacy=C), snapshot)

    assert site is not None and site["feed_circuit_id"] == C and site["power_entity_id"] == POWER[C]


def test_without_pv_there_is_no_block() -> None:
    snapshot = SpanPanelSnapshotFactory.create()
    assert not has_pv(snapshot)

    assert _site(pv_binding_for(snapshot), snapshot) is None
    assert solar_topology("anything", pv_binding_for(snapshot), snapshot, POWER, SITE) is None
