"""Which inverter the Solar card's PV entities read, decided over plain snapshots."""

from __future__ import annotations

from dataclasses import replace
from itertools import combinations
from typing import Final

from span_panel_api import SpanPanelSnapshot, SpanPVSnapshot

from custom_components.span_panel.pv_binding import (
    UNDECIDED_PV_BINDING,
    StoredPvBinding,
    read_record,
    resolve,
)

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory

CIRCUIT: Final = "573066aaddd7b75114c4563ce3af18c4"
OTHER_CIRCUIT: Final = "5be1d2c3a4f5061728394a5b6c7d8e9f"
BOUND: Final = StoredPvBinding(circuit_id=CIRCUIT)
UNBOUND: Final = StoredPvBinding(circuit_id=None)


def _fed(device_id: str, circuit: str) -> SpanPVSnapshot:
    return SpanPVSnapshot(
        vendor_name="Enphase", device_id=device_id, node_id=circuit, feed_circuit_id=circuit
    )


def _unfed(device_id: str) -> SpanPVSnapshot:
    return SpanPVSnapshot(vendor_name="SolarEdge", device_id=device_id, node_id=device_id)


def _snapshot(
    *inverters: SpanPVSnapshot, circuits: tuple[str, ...] = (CIRCUIT,)
) -> SpanPanelSnapshot:
    keyed: dict[str, SpanPVSnapshot] = {}
    for inverter in inverters:
        assert inverter.node_id is not None
        keyed[inverter.node_id] = inverter
    lone = inverters[0] if len(inverters) == 1 else SpanPVSnapshot(vendor_name="together")
    return replace(
        SpanPanelSnapshotFactory.create(),
        pv_inverters=keyed,
        pv=lone,
        circuits={
            circuit: SpanCircuitSnapshotFactory.create(circuit_id=circuit) for circuit in circuits
        },
    )


# --- first sight -------------------------------------------------------------------


def test_one_circuit_fed_inverter_is_bound_at_first_sight() -> None:
    identity, record = resolve(_snapshot(_fed("pv", CIRCUIT)), None, frozenset())

    assert record == BOUND
    assert identity.mode == "inverter"
    assert identity.legacy_key == CIRCUIT
    assert not identity.has_own_card(CIRCUIT)


def test_an_inverter_no_circuit_feeds_is_unbound_and_read_by_the_solar_card() -> None:
    identity, record = resolve(_snapshot(_unfed("panel-se7600h-us")), None, frozenset())

    assert record == UNBOUND
    assert identity.legacy_key == "panel-se7600h-us"
    assert not identity.has_own_card("panel-se7600h-us")


def test_several_inverters_at_first_sight_are_unbound_and_each_has_a_card() -> None:
    identity, record = resolve(
        _snapshot(_fed("a", CIRCUIT), _fed("b", OTHER_CIRCUIT)), None, frozenset()
    )

    assert record == UNBOUND
    assert identity.legacy_key is None
    assert identity.has_own_card(CIRCUIT)
    assert identity.has_own_card(OTHER_CIRCUIT)


def test_no_inverter_decides_nothing() -> None:
    identity, record = resolve(_snapshot(), None, frozenset())

    assert record is None
    assert identity == UNDECIDED_PV_BINDING


# --- the record is kept, and refined once -----------------------------------------------


def test_a_bound_record_is_never_rewritten() -> None:
    _identity, record = resolve(
        _snapshot(_fed("pv", OTHER_CIRCUIT), circuits=(OTHER_CIRCUIT,)), BOUND, frozenset()
    )

    assert record == BOUND


def test_an_unbound_record_is_refined_when_the_lone_inverter_gains_a_circuit_and_has_no_card() -> (
    None
):
    identity, record = resolve(_snapshot(_fed("pv", CIRCUIT)), UNBOUND, frozenset())

    assert record == BOUND
    assert identity.legacy_key == CIRCUIT


def test_an_unbound_record_is_not_refined_for_an_inverter_that_has_a_card() -> None:
    identity, record = resolve(_snapshot(_fed("pv", CIRCUIT)), UNBOUND, frozenset({CIRCUIT}))

    assert record == UNBOUND
    assert identity.legacy_key is None


def test_an_unbound_record_is_not_refined_with_several_inverters() -> None:
    _identity, record = resolve(
        _snapshot(_fed("a", CIRCUIT), _fed("b", OTHER_CIRCUIT)), UNBOUND, frozenset()
    )

    assert record == UNBOUND


# --- what the Solar card reads ------------------------------------------------------


def test_a_bound_card_reads_its_circuit_beside_a_newcomer() -> None:
    first, second = _fed("pv-1", CIRCUIT), _fed("pv-2", OTHER_CIRCUIT)
    snapshot = _snapshot(first, second, circuits=(CIRCUIT, OTHER_CIRCUIT))

    identity, _record = resolve(snapshot, BOUND, frozenset())

    assert identity.source(snapshot) is first
    assert identity.has_own_card(OTHER_CIRCUIT)
    assert not identity.has_own_card(CIRCUIT)


def test_an_unbound_card_reads_the_librarys_pv() -> None:
    snapshot = _snapshot(_unfed("d1"), _unfed("d2"))

    identity, _record = resolve(snapshot, UNBOUND, frozenset())

    assert identity.source(snapshot) is snapshot.pv


# --- a pending record --------------------------------------------------------------


def test_a_bound_circuit_still_published_without_its_inverter_withholds_device_id_keys() -> None:
    """The record has not arrived: nothing is minted for the device-id key, and the card reads unknown."""
    snapshot = _snapshot(_unfed("pv"), circuits=(CIRCUIT,))

    identity, record = resolve(snapshot, BOUND, frozenset())

    assert record == BOUND
    assert identity.withheld == frozenset({"pv"})
    assert not identity.has_own_card("pv")
    assert identity.source(snapshot) == SpanPVSnapshot()


def test_a_bound_circuit_that_is_gone_withholds_nothing() -> None:
    """The panel replaced the circuit: the inverter gets a card and the Solar card reads unknown."""
    snapshot = _snapshot(_fed("pv", OTHER_CIRCUIT), circuits=(OTHER_CIRCUIT,))

    identity, _record = resolve(snapshot, BOUND, frozenset())

    assert identity.withheld == frozenset()
    assert identity.has_own_card(OTHER_CIRCUIT)
    assert identity.source(snapshot) == SpanPVSnapshot()


def test_pending_never_withdraws_a_card_that_exists() -> None:
    """N1: an upstream PV that already has a card keeps it while the bound circuit's record is pending."""
    snapshot = _snapshot(_unfed("upstream"), circuits=(CIRCUIT,))

    identity, _record = resolve(snapshot, BOUND, frozenset({"upstream"}))

    assert identity.withheld == frozenset()
    assert identity.has_own_card("upstream")


def test_first_sight_never_binds_an_inverter_that_has_a_card() -> None:
    """N2: a store lost or corrupt while cards exist leaves the lone carded inverter on its card, unbound."""
    identity, record = resolve(_snapshot(_fed("pv", CIRCUIT)), None, frozenset({CIRCUIT}))

    assert record == UNBOUND
    assert identity.has_own_card(CIRCUIT)


def test_a_held_key_always_keeps_its_card() -> None:
    """The invariant behind "nothing moves": for every key in `held`, `has_own_card` is True.

    For every subset of each snapshot's inverters as `held`, across first sight
    (no record, which is also a removed or corrupt store), an unbound record, a
    bound record and a pending bound record.
    """
    snapshots = (
        _snapshot(_fed("pv", CIRCUIT)),
        _snapshot(_unfed("pv")),
        _snapshot(_fed("a", CIRCUIT), _fed("b", OTHER_CIRCUIT), circuits=(CIRCUIT, OTHER_CIRCUIT)),
        _snapshot(_unfed("pv"), circuits=(CIRCUIT,)),
        _snapshot(_fed("x", OTHER_CIRCUIT), _unfed("u"), circuits=(CIRCUIT, OTHER_CIRCUIT)),
    )
    for snapshot in snapshots:
        keys = sorted(snapshot.pv_inverters)
        for size in range(len(keys) + 1):
            for subset in combinations(keys, size):
                held = frozenset(subset)
                for record in (None, UNBOUND, BOUND):
                    identity, _record = resolve(snapshot, record, held)
                    for key in held:
                        assert identity.has_own_card(key), (sorted(held), record, key)


# --- the record on disk ---------------------------------------------------------------


def test_a_wrong_shaped_record_reads_as_absent() -> None:
    for stored in (None, [], "text", 3, {}, {"circuit_id": 7}):
        assert read_record(stored) is None
    assert read_record({"circuit_id": CIRCUIT}) == BOUND
    assert read_record({"circuit_id": None}) == UNBOUND
