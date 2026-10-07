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


def _fed(device_id: str, circuit: str, *, linked: bool | None = None) -> SpanPVSnapshot:
    """`linked` is the circuit's link record: `None` where it publishes none."""
    return SpanPVSnapshot(
        vendor_name="Enphase",
        device_id=device_id,
        node_id=circuit,
        feed_circuit_id=circuit,
        connected=linked,
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
    identity, record = resolve(_snapshot(_fed("pv", CIRCUIT)), None, frozenset(), link_held=False, inverter_links_held=frozenset())

    assert record == BOUND
    assert identity.mode == "inverter"
    assert identity.legacy_key == CIRCUIT
    assert not identity.has_own_card(CIRCUIT)


def test_an_inverter_no_circuit_feeds_is_unbound_and_read_by_the_solar_card() -> None:
    identity, record = resolve(_snapshot(_unfed("panel-se7600h-us")), None, frozenset(), link_held=False, inverter_links_held=frozenset())

    assert record == UNBOUND
    assert identity.legacy_key == "panel-se7600h-us"
    assert not identity.has_own_card("panel-se7600h-us")


def test_several_inverters_at_first_sight_are_unbound_and_each_has_a_card() -> None:
    identity, record = resolve(
        _snapshot(_fed("a", CIRCUIT), _fed("b", OTHER_CIRCUIT)), None, frozenset(), link_held=False, inverter_links_held=frozenset()
    )

    assert record == UNBOUND
    assert identity.legacy_key is None
    assert identity.has_own_card(CIRCUIT)
    assert identity.has_own_card(OTHER_CIRCUIT)


def test_no_inverter_decides_nothing() -> None:
    identity, record = resolve(_snapshot(), None, frozenset(), link_held=False, inverter_links_held=frozenset())

    assert record is None
    assert identity == UNDECIDED_PV_BINDING


# --- the record is kept, and refined once -----------------------------------------------


def test_a_bound_record_is_never_rewritten() -> None:
    _identity, record = resolve(
        _snapshot(_fed("pv", OTHER_CIRCUIT), circuits=(OTHER_CIRCUIT,)), BOUND, frozenset(), link_held=False, inverter_links_held=frozenset()
    )

    assert record == BOUND


def test_an_unbound_record_is_refined_when_the_lone_inverter_gains_a_circuit_and_has_no_card() -> (
    None
):
    identity, record = resolve(_snapshot(_fed("pv", CIRCUIT)), UNBOUND, frozenset(), link_held=False, inverter_links_held=frozenset())

    assert record == BOUND
    assert identity.legacy_key == CIRCUIT


def test_an_unbound_record_is_not_refined_for_an_inverter_that_has_a_card() -> None:
    identity, record = resolve(_snapshot(_fed("pv", CIRCUIT)), UNBOUND, frozenset({CIRCUIT}), link_held=False, inverter_links_held=frozenset())

    assert record == UNBOUND
    assert identity.legacy_key is None


def test_an_unbound_record_is_not_refined_with_several_inverters() -> None:
    _identity, record = resolve(
        _snapshot(_fed("a", CIRCUIT), _fed("b", OTHER_CIRCUIT)), UNBOUND, frozenset(), link_held=False, inverter_links_held=frozenset()
    )

    assert record == UNBOUND


# --- what the Solar card reads ------------------------------------------------------


def test_a_bound_card_reads_its_circuit_beside_a_newcomer() -> None:
    first, second = _fed("pv-1", CIRCUIT), _fed("pv-2", OTHER_CIRCUIT)
    snapshot = _snapshot(first, second, circuits=(CIRCUIT, OTHER_CIRCUIT))

    identity, _record = resolve(snapshot, BOUND, frozenset(), link_held=False, inverter_links_held=frozenset())

    assert identity.source(snapshot) is first
    assert identity.has_own_card(OTHER_CIRCUIT)
    assert not identity.has_own_card(CIRCUIT)


def test_an_unbound_card_reads_the_librarys_pv() -> None:
    snapshot = _snapshot(_unfed("d1"), _unfed("d2"))

    identity, _record = resolve(snapshot, UNBOUND, frozenset(), link_held=False, inverter_links_held=frozenset())

    assert identity.source(snapshot) is snapshot.pv


# --- a pending record --------------------------------------------------------------


def test_a_bound_circuit_still_published_without_its_inverter_withholds_device_id_keys() -> None:
    """The record has not arrived: nothing is minted for the device-id key, and the card reads unknown."""
    snapshot = _snapshot(_unfed("pv"), circuits=(CIRCUIT,))

    identity, record = resolve(snapshot, BOUND, frozenset(), link_held=False, inverter_links_held=frozenset())

    assert record == BOUND
    assert identity.withheld == frozenset({"pv"})
    assert not identity.has_own_card("pv")
    assert identity.source(snapshot) == SpanPVSnapshot()


def test_a_bound_circuit_that_is_gone_withholds_nothing() -> None:
    """The panel replaced the circuit: the inverter gets a card and the Solar card reads unknown."""
    snapshot = _snapshot(_fed("pv", OTHER_CIRCUIT), circuits=(OTHER_CIRCUIT,))

    identity, _record = resolve(snapshot, BOUND, frozenset(), link_held=False, inverter_links_held=frozenset())

    assert identity.withheld == frozenset()
    assert identity.has_own_card(OTHER_CIRCUIT)
    assert identity.source(snapshot) == SpanPVSnapshot()


def test_pending_never_withdraws_a_card_that_exists() -> None:
    """N1: an upstream PV that already has a card keeps it while the bound circuit's record is pending."""
    snapshot = _snapshot(_unfed("upstream"), circuits=(CIRCUIT,))

    identity, _record = resolve(snapshot, BOUND, frozenset({"upstream"}), link_held=False, inverter_links_held=frozenset())

    assert identity.withheld == frozenset()
    assert identity.has_own_card("upstream")


def test_first_sight_never_binds_an_inverter_that_has_a_card() -> None:
    """N2: a store lost or corrupt while cards exist leaves the lone carded inverter on its card, unbound."""
    identity, record = resolve(_snapshot(_fed("pv", CIRCUIT)), None, frozenset({CIRCUIT}), link_held=False, inverter_links_held=frozenset())

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
                    identity, _record = resolve(snapshot, record, held, link_held=False, inverter_links_held=frozenset())
                    for key in held:
                        assert identity.has_own_card(key), (sorted(held), record, key)


# --- the record on disk ---------------------------------------------------------------


def test_a_wrong_shaped_record_reads_as_absent() -> None:
    for stored in (None, [], "text", 3, {}, {"circuit_id": 7}):
        assert read_record(stored) is None
    assert read_record({"circuit_id": CIRCUIT}) == BOUND
    assert read_record({"circuit_id": None}) == UNBOUND


# --- whether the Solar card has a link entity -------------------------------------
#
# Decided by what is stable, never by a reading: the entity is already registered
# (it never vanishes), or every inverter the card reads publishes a link record.


def _solar_link(snapshot: SpanPanelSnapshot, record: StoredPvBinding | None, *, link_held: bool) -> bool:
    return resolve(snapshot, record, frozenset(), link_held=link_held, inverter_links_held=frozenset())[0].solar_link


def test_a_lone_inverter_has_a_solar_link_exactly_when_its_circuit_publishes_a_record() -> None:
    """2.1.1 parity: 2.1.1 created the link iff the lone inverter's circuit published one."""
    assert _solar_link(_snapshot(_fed("pv", CIRCUIT, linked=True)), None, link_held=False)
    assert _solar_link(_snapshot(_fed("pv", CIRCUIT, linked=False)), None, link_held=False)
    assert not _solar_link(_snapshot(_fed("pv", CIRCUIT)), None, link_held=False)
    assert not _solar_link(_snapshot(_unfed("pv")), None, link_held=False)


def test_a_bound_solar_link_follows_the_bound_inverters_record() -> None:
    first, second = _fed("a", CIRCUIT, linked=True), _fed("b", OTHER_CIRCUIT)
    assert _solar_link(_snapshot(first, second, circuits=(CIRCUIT, OTHER_CIRCUIT)), BOUND, link_held=False)
    assert not _solar_link(
        _snapshot(_fed("a", CIRCUIT), _fed("b", OTHER_CIRCUIT, linked=True), circuits=(CIRCUIT, OTHER_CIRCUIT)),
        BOUND,
        link_held=False,
    )


def test_a_bound_inverter_that_is_not_published_keeps_a_registered_link() -> None:
    """Pending or departed: (b) cannot hold, so only the registry keeps the entity, and it does."""
    pending = _snapshot(_unfed("pv"), circuits=(CIRCUIT,))
    departed = _snapshot(_fed("x", OTHER_CIRCUIT, linked=True), circuits=(OTHER_CIRCUIT,))
    for snapshot in (pending, departed):
        assert not _solar_link(snapshot, BOUND, link_held=False)
        assert _solar_link(snapshot, BOUND, link_held=True)


def test_an_unbound_link_over_several_does_not_follow_a_reading() -> None:
    """One record-less inverter: whether the others are up or down, the answer is the same."""
    for linked in (True, False):
        snapshot = _snapshot(_fed("a", CIRCUIT, linked=linked), _fed("b", OTHER_CIRCUIT))
        assert not _solar_link(snapshot, UNBOUND, link_held=False)
        assert _solar_link(snapshot, UNBOUND, link_held=True)


def test_an_unbound_link_over_several_all_recorded_is_created() -> None:
    for first, second in ((True, True), (True, False), (False, False)):
        snapshot = _snapshot(_fed("a", CIRCUIT, linked=first), _fed("b", OTHER_CIRCUIT, linked=second))
        assert _solar_link(snapshot, UNBOUND, link_held=False)


def test_with_no_inverter_only_a_registered_link_is_kept() -> None:
    assert resolve(_snapshot(), None, frozenset(), link_held=False, inverter_links_held=frozenset())[0] == UNDECIDED_PV_BINDING
    assert resolve(_snapshot(), None, frozenset(), link_held=True, inverter_links_held=frozenset())[0].solar_link


# --- whether an inverter's own card has its link entity ------------------------------
#
# The Solar link's rule, applied per key: a link already registered is always created;
# otherwise the inverter's own record decides.


def _inverter_links(
    snapshot: SpanPanelSnapshot, record: StoredPvBinding | None, *, links_held: frozenset[str]
) -> frozenset[str]:
    # A registered link is one of the entities a card carries, so its key holds a card too.
    return resolve(snapshot, record, links_held, link_held=False, inverter_links_held=links_held)[0].inverter_links


def test_a_carded_inverter_has_its_link_where_its_circuit_publishes_a_record() -> None:
    snapshot = _snapshot(
        _fed("a", CIRCUIT, linked=True), _fed("b", OTHER_CIRCUIT), circuits=(CIRCUIT, OTHER_CIRCUIT)
    )
    assert _inverter_links(snapshot, UNBOUND, links_held=frozenset()) == frozenset({CIRCUIT})


def test_a_registered_inverter_link_is_kept_when_its_record_goes() -> None:
    snapshot = _snapshot(_fed("a", CIRCUIT), _fed("b", OTHER_CIRCUIT), circuits=(CIRCUIT, OTHER_CIRCUIT))
    assert _inverter_links(snapshot, UNBOUND, links_held=frozenset({OTHER_CIRCUIT})) == frozenset({OTHER_CIRCUIT})


def test_only_an_inverter_with_its_own_card_has_its_own_link() -> None:
    """The Solar card's inverter has no card of its own, so no own link, though it publishes a record; nor has a withheld one."""
    bound = _snapshot(
        _fed("a", CIRCUIT, linked=True), _fed("b", OTHER_CIRCUIT, linked=True), circuits=(CIRCUIT, OTHER_CIRCUIT)
    )
    assert _inverter_links(bound, BOUND, links_held=frozenset()) == frozenset({OTHER_CIRCUIT})
    pending = _snapshot(_fed("x", OTHER_CIRCUIT), _unfed("pv"), circuits=(CIRCUIT, OTHER_CIRCUIT))
    assert _inverter_links(pending, BOUND, links_held=frozenset()) == frozenset()
