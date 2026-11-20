"""A panel update that gives its circuits new device ids keeps everything an install holds.

Firmware r202645 calls a branch circuit `{prefix}-1-16-<n>` that r202639 called
`circuit-<n>`, and a remote CT `{prefix}-1-42-<n>` for `remote-ct-<n>`. These
tests run the integration's real setup -- coordinator, platforms, registries,
stores -- over SPAN's reference captures of the same panel before and after
(`r202639-e` and `r202645-e`, and `r202639-a` and `r202645-a`, whose remote CT
is the first captured on r202645), over a recommissioned pair (`r202639-c`,
`r202645-c`), and over trees derived from the captures where no capture shows
the case: an inverter a circuit feeds, a vendor extension on a circuit, a
partial tree, collisions an earlier release left, and a registry from before
circuits had devices. The vendored captures are never edited; a derived
tree is built in memory by `captures_replay.renamed_devices`.

Each test names, in its docstring, the change to the integration that makes it
fail.
"""

from __future__ import annotations

from typing import Final

import pytest
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel.device_id_migration import (
    current_circuit_id,
    migration_pending,
    previous_device_ids,
    rekeyed,
)

from .captures_replay import (
    capture,
    snapshot,
)
from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory

BEFORE: Final = "r202639-e"
AFTER: Final = "r202645-e"
"""The same panel on r202639 and on r202645: 46 circuits that pair by trait instance."""

TANDEM: Final = ("circuit-52", "circuit-53")
"""Two circuits sharing one meter; monitoring holds the pair's override under the first."""

RANGE: Final = "circuit-5"
"""`Electric range`, 20 A on space 9: one circuit on its own."""

BATH: Final = "circuit-7"
"""`Primary Bath Outlets`, whose device the user renames and puts in an area."""

HVAC: Final = "circuit-1"
"""`HVAC`, whose breaker switch carries an ignored area notice."""

CIRCUIT_TYPE: Final = "energy.ebus.device.circuit"

MIGRATION_LOGGER: Final = "custom_components.span_panel.device_id_rekey"
MAPPING_LOGGER: Final = "custom_components.span_panel.device_id_migration"

_NOTICES_KEY: Final = "span_panel.notices"
_PACKAGES_KEY: Final = "span_panel_package_ratings"
_PACKAGE_NOTICE: Final = "package_ratings_changed"
_NEW_ENTITIES_NOTICE: Final = "new_entities"


OLD_IDS_E: Final = frozenset(snapshot(capture(BEFORE).tree()).circuits)
"""Every circuit id of `r202639-e`."""

_FAMILY_OF_TRAIT: Final = {"16": "circuit", "42": "remote-ct"}


def new_ids(name: str) -> dict[str, str]:
    """Return each r202639 id's counterpart in an r202645 capture, read off the capture's own ids.

    Read from the capture rather than written out, so a capture whose ids are
    masked afresh needs no edit here. Read by splitting the id, not through the
    integration's pairing, which is what the tests hold to account.
    """
    ids: dict[str, str] = {}
    for circuit_id in snapshot(capture(name).tree()).circuits:
        head, trait, n = circuit_id.rsplit("-", 2)
        assert head.endswith("-1") and trait in _FAMILY_OF_TRAIT, circuit_id
        ids[f"{_FAMILY_OF_TRAIT[trait]}-{n}"] = circuit_id
    return ids


NEW_IDS_E: Final = new_ids(AFTER)


def new_id_e(old_id: str) -> str:
    """Return the id `r202645-e` gives the circuit `r202639-e` calls `old_id`."""
    return NEW_IDS_E[old_id]


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


# ---------------------------------------------------------------------------
# the mapping itself
# ---------------------------------------------------------------------------


def panel_of(*circuit_ids: str) -> SpanPanelSnapshot:
    """Return a panel holding these circuits."""
    return SpanPanelSnapshotFactory.create(
        circuits={cid: SpanCircuitSnapshotFactory.create(circuit_id=cid) for cid in circuit_ids}
    )


class TestPreviousDeviceIds:
    """The pairing reads the trait suffix and nothing else.

    Fails when the anchor is loosened, a prefix check is added, or the duplicate
    guard is removed.
    """

    def test_a_branch_circuit_and_a_remote_ct_pair_by_trait_instance(self) -> None:
        """`-1-16-<n>` pairs with `circuit-<n>`, and `-1-42-<n>` with `remote-ct-<n>`."""
        assert previous_device_ids(panel_of("p-1-16-5", "p-1-42-3")) == {
            "circuit-5": "p-1-16-5",
            "remote-ct-3": "p-1-42-3",
        }

    def test_a_prefix_that_is_not_the_root_still_pairs(self) -> None:
        """The prefix is never checked against the root's id."""
        assert previous_device_ids(panel_of("not-the-root-0123-1-16-9")) == {
            "circuit-9": "not-the-root-0123-1-16-9"
        }

    def test_a_serial_containing_hyphens_still_pairs(self) -> None:
        """Only the trait suffix is read, so hyphens in front of it are nobody's concern."""
        assert previous_device_ids(panel_of("ab-12-cd-34-ffffffffffffffff-1-16-12")) == {
            "circuit-12": "ab-12-cd-34-ffffffffffffffff-1-16-12"
        }

    @pytest.mark.parametrize(
        "circuit_id",
        [
            "p-1-17-5",
            "1-16-5",
            "p-1-16-5a",
            "p-1-16-",
            "p-2-16-5",
            "0123456789abcdef0123456789abcdef",
            "circuit-5",
            "p-1-16-5\n",
        ],
    )
    def test_anything_else_pairs_nothing(self, circuit_id: str) -> None:
        """Another trait, no prefix, a trailing non-digit, an opaque id, an old id: no pair."""
        assert previous_device_ids(panel_of(circuit_id)) == {}

    def test_two_ids_claiming_one_trait_instance_refuse_the_whole_mapping(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A guess between them would carry one circuit's history onto another."""
        assert previous_device_ids(panel_of("a-1-16-5", "b-1-16-5", "a-1-16-6")) == {}
        assert "share trait instance circuit-5" in caplog.text

    def test_every_capture_of_a_new_tree_pairs_each_of_its_circuits(self) -> None:
        """Every circuit of every r202645 capture has an old id to pair with."""
        for name in ("r202645-c", "r202645-e", "r202645-g"):
            circuits = snapshot(capture(name).tree()).circuits
            assert sorted(previous_device_ids(snapshot(capture(name).tree())).values()) == sorted(
                circuits
            ), name

    def test_no_capture_of_an_older_tree_pairs_anything(self) -> None:
        """No r202639 or MAIN 32 capture holds an id of the new form."""
        for name in (
            "main32_r202633",
            "main32_r202639",
            "main32_r202645",
            "main32_r202645-cascade",
            "main32_r202639-upstream-pv",
            "r202639-a",
            "r202639-b",
            "r202639-c",
            "r202639-d",
            "r202639-e",
        ):
            assert previous_device_ids(snapshot(capture(name).tree())) == {}, name


class TestMigrationPending:
    """The guard's predicate: a missing session circuit with an old-form counterpart."""

    def test_new_ids_for_the_sessions_circuits(self) -> None:
        """The session's circuit is missing, and the snapshot pairs it with a new id."""
        assert migration_pending(frozenset({"circuit-5"}), panel_of("p-1-16-5"))

    def test_a_circuit_gone_from_a_panel_on_new_ids(self) -> None:
        """A circuit missing from a panel already on new ids has no counterpart."""
        assert not migration_pending(frozenset({"p-1-16-5", "p-1-16-6"}), panel_of("p-1-16-5"))

    def test_a_circuit_gone_from_a_panel_on_opaque_ids(self) -> None:
        """A circuit missing from a panel on opaque ids has no counterpart either."""
        opaque = "0123456789abcdef0123456789abcdef"
        assert not migration_pending(frozenset({opaque, "x"}), panel_of(opaque))

    def test_the_same_circuits(self) -> None:
        """Nothing missing, nothing pending."""
        assert not migration_pending(frozenset({"circuit-5"}), panel_of("circuit-5"))


class TestCurrentCircuitId:
    """What a service call's raw id means now."""

    def test_an_old_id_answers_its_new_one(self, caplog: pytest.LogCaptureFixture) -> None:
        """Applied to the circuit under its new id, and logged with both ids."""
        assert current_circuit_id(panel_of("p-1-16-5"), "circuit-5", service="s") == "p-1-16-5"
        assert "circuit-5" in caplog.text and "p-1-16-5" in caplog.text

    def test_a_live_id_and_an_unknown_one_answer_themselves(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Only an old id the snapshot pairs is rewritten, and nothing else is logged."""
        live = panel_of("p-1-16-5")
        assert current_circuit_id(live, "p-1-16-5", service="s") == "p-1-16-5"
        assert current_circuit_id(live, "circuit-6", service="s") == "circuit-6"
        assert current_circuit_id(live, "sensor.kitchen_power", service="s") == (
            "sensor.kitchen_power"
        )
        assert not caplog.records


def test_a_value_under_the_new_id_wins_over_the_old() -> None:
    """The stores' collision rule: a value set after the update outranks the one it replaces."""
    assert rekeyed({"old": 1, "new": 2, "other": 3}, {"old": "new"}) == ({"new": 2, "other": 3}, 1)
    assert rekeyed({"old": 1, "other": 3}, {"old": "new"}) == ({"other": 3, "new": 1}, 1)
    assert rekeyed({"other": 3}, {"old": "new"}) == ({"other": 3}, 0)
