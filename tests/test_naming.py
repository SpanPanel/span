"""The base Home Assistant composes a circuit entity's id from."""

from __future__ import annotations

import pytest

from custom_components.span_panel.naming import (
    ENTITY_ID_SUFFIX_FORMS,
    NEW_ENTITY_ID_SUFFIX_WORDS,
    circuit_object_id_base,
)


@pytest.mark.parametrize(
    ("identifier", "suffix", "expected"),
    [
        ("Circuit 15", "power", "Circuit 15 power"),
        ("Circuit 30 32", "energy_consumed", "Circuit 30 32 consumed energy"),
        ("Kitchen Outlets", "energy_produced", "Kitchen Outlets produced energy"),
        ("Kitchen Outlets", "energy_net", "Kitchen Outlets net energy"),
        ("Kitchen Outlets", "current", "Kitchen Outlets current"),
        ("Kitchen Outlets", "breaker_rating", "Kitchen Outlets breaker rating"),
        ("Kitchen Outlets", "breaker", "Kitchen Outlets breaker"),
        ("Kitchen Outlets", "circuit_priority", "Kitchen Outlets circuit priority"),
        ("Unmapped Tab 32", "energy_consumed", "Unmapped Tab 32 consumed energy"),
    ],
)
def test_a_new_entity_gets_the_noun_last_wording(
    identifier: str, suffix: str, expected: str
) -> None:
    assert circuit_object_id_base(identifier, suffix, None) == expected


@pytest.mark.parametrize(
    ("existing", "suffix", "expected"),
    [
        # Preset-era spelling is kept, so Recreate proposes the entity its own id.
        (
            "sensor.span_panel_circuit_15_energy_consumed",
            "energy_consumed",
            "Circuit 15 energy consumed",
        ),
        (
            "sensor.span_panel_circuit_15_energy_produced",
            "energy_produced",
            "Circuit 15 energy produced",
        ),
        ("sensor.span_panel_circuit_15_energy_net", "energy_net", "Circuit 15 energy net"),
        # Pre-preset spelling is kept too.
        (
            "sensor.span_panel_circuit_15_consumed_energy",
            "energy_consumed",
            "Circuit 15 consumed energy",
        ),
        # The oldest power spelling.
        ("sensor.span_panel_circuit_15_current_power", "power", "Circuit 15 current power"),
        # A suffix with one form only.
        ("switch.span_panel_circuit_15_breaker", "breaker", "Circuit 15 breaker"),
    ],
)
def test_an_existing_entity_keeps_the_suffix_wording_it_has(
    existing: str, suffix: str, expected: str
) -> None:
    assert circuit_object_id_base("Circuit 15", suffix, existing) == expected


def test_an_existing_entity_whose_id_matches_no_known_form_gets_the_default() -> None:
    assert (
        circuit_object_id_base("Circuit 15", "energy_consumed", "sensor.something_else")
        == "Circuit 15 consumed energy"
    )


def test_the_words_are_omitted_when_the_identifier_already_ends_with_them() -> None:
    """Preserves the preset builder's rule: a circuit named "Solar Power" got `..._solar_power`."""
    assert circuit_object_id_base("Solar Power", "power", None) == "Solar Power"
    assert (
        circuit_object_id_base("Solar Power", "power", "sensor.span_panel_solar_power")
        == "Solar Power"
    )


def test_every_form_table_entry_has_a_new_wording_and_vice_versa() -> None:
    assert set(ENTITY_ID_SUFFIX_FORMS) == set(NEW_ENTITY_ID_SUFFIX_WORDS)
    for suffix, words in NEW_ENTITY_ID_SUFFIX_WORDS.items():
        assert words.replace(" ", "_") in ENTITY_ID_SUFFIX_FORMS[suffix], (
            f"the default wording for {suffix} must itself be a known form"
        )
