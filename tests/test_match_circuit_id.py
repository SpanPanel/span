"""A circuit id is opaque: it is found in a unique id as a whole run of `_` segments.

The public changelog says to treat a device id as opaque. Matching by substring with
`-` as a boundary found `b-7` inside `sub-b-7` whenever `b-7` was tried first, and
matching by shape found only ids of 32 hex characters.
"""

from __future__ import annotations

import pytest

from custom_components.span_panel.helpers import match_circuit_id as reexported
from custom_components.span_panel.id_builder import (
    build_circuit_unique_id,
    build_switch_unique_id,
    match_circuit_id,
)

HEX = "0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize("ids", [["b-7", "sub-b-7"], ["sub-b-7", "b-7"]])
def test_a_hyphen_suffix_id_never_matches_inside_a_longer_one(ids: list[str]) -> None:
    assert match_circuit_id("span_nt-0000-test1_sub-b-7_power", ids) == "sub-b-7"
    assert match_circuit_id("span_nt-0000-test1_b-7_power", ids) == "b-7"


def test_a_numeric_suffix_pair() -> None:
    ids = {"c-1", "c-12"}
    assert match_circuit_id("span_nt-0000-test1_c-12_energy_consumed", ids) == "c-12"
    assert match_circuit_id("span_nt-0000-test1_c-1_energy_consumed", ids) == "c-1"


def test_a_hex_id() -> None:
    assert match_circuit_id(f"span_nt-0000-test1_{HEX}_power", {HEX}) == HEX


def test_switch_and_select_ids() -> None:
    assert match_circuit_id("span_nt-0000-test1_relay_b-7", {"b-7"}) == "b-7"
    assert match_circuit_id("span_nt-0000-test1_select_b-7", {"b-7"}) == "b-7"


def test_underscore_ids_resolve_longest_run() -> None:
    """An id that is a prefix of another, or one containing `_`, resolves to the one meant."""
    ids = {"ab", "ab_cd"}
    assert match_circuit_id("span_nt-0000-test1_ab_cd_power", ids) == "ab_cd"
    assert match_circuit_id("span_nt-0000-test1_ab_power", ids) == "ab"


def test_no_snapshot_ids_means_no_match_never_a_guess() -> None:
    assert match_circuit_id(f"span_nt-0000-test1_{HEX}_power", ()) is None


def test_a_panel_level_unique_id_matches_nothing() -> None:
    assert match_circuit_id("span_nt-0000-test1_instantGridPowerW", {"b-7", HEX}) is None


def test_the_serial_is_never_read_as_a_circuit_id() -> None:
    """The prefix is skipped, so a serial that equals a circuit id cannot shadow the real one."""
    assert match_circuit_id("span_b-7_c-1_power", {"b-7", "c-1"}) == "c-1"


def test_every_id_the_builders_mint_resolves_to_its_circuit() -> None:
    """Parsing is the inverse of construction, which is unchanged."""
    ids = {"b-7", "sub-b-7", "c-1", "c-12", HEX}
    for circuit_id in ids:
        for key in ("instantPowerW", "consumedEnergyWh", "current", "breaker_rating"):
            unique_id = build_circuit_unique_id("NT-0000-TEST1", circuit_id, key)
            assert match_circuit_id(unique_id, ids) == circuit_id
        assert match_circuit_id(build_switch_unique_id("NT-0000-TEST1", circuit_id), ids) == (
            circuit_id
        )


def test_helpers_re_exports_the_matcher() -> None:
    assert reexported is match_circuit_id
