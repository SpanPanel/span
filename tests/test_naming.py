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


def test_an_identifier_that_is_only_the_suffix_word_keeps_both_halves() -> None:
    """The builder's test was `endswith(f"_{suffix}")`, underscore included.

    A circuit named exactly "Power" therefore got `..._power_power`, because
    "power" does not end with "_power" and there was nothing to omit. Omitting
    it here would offer every such circuit a rename to `..._power` -- a move the
    user did not cause, which R1 forbids.
    """
    assert circuit_object_id_base("Power", "power", None) == "Power power"
    assert (
        circuit_object_id_base("Power", "power", "sensor.span_panel_power_power")
        == "Power power"
    )


def test_a_renamed_circuit_takes_the_new_name_and_reads_the_suffix_from_its_id() -> None:
    """Issue #252 at the layer that decides it: only the name half follows the panel.

    The identifier is the circuit's *current* name; the id it already carries is
    consulted for nothing but which spelling of the suffix it shipped with. So a
    circuit renamed in the SPAN app is proposed the new name and the same suffix.
    """
    assert (
        circuit_object_id_base("Kitchen", "power", "sensor.span_panel_kitchen_outlets_power")
        == "Kitchen power"
    )


@pytest.mark.parametrize(
    ("identifier", "suffix", "existing", "expected"),
    [
        # A circuit named "Current". The id's trailing `_current_power` is the
        # identifier followed by `power`, not the `current_power` spelling of the
        # suffix -- reading it as the latter proposed `..._current_current_power`.
        ("Current", "power", "sensor.span_panel_current_power", "Current power"),
        # A circuit named "Solar Power" whose id kept both halves. The omission
        # rule looks only at the identifier, so it proposed `..._solar_power` and
        # offered every such circuit a rename.
        ("Solar Power", "power", "sensor.span_panel_solar_power_power", "Solar Power power"),
        # The same circuit on an install whose id did omit them.
        ("Solar Power", "power", "sensor.span_panel_solar_power", "Solar Power"),
        # Both, on an install with no device prefix: the identifier opens the
        # object id rather than following an underscore.
        ("Solar Power", "power", "sensor.solar_power_power", "Solar Power power"),
        ("Solar Power", "power", "sensor.solar_power", "Solar Power"),
        # A circuit named exactly "Power", which never omitted anything.
        ("Power", "power", "sensor.span_panel_power_power", "Power power"),
    ],
)
def test_the_suffix_is_read_back_from_what_follows_the_identifier(
    identifier: str, suffix: str, existing: str, expected: str
) -> None:
    """Where the id names this circuit, what follows the name settles both halves.

    Both halves, because they are one question: an id that spells the identifier
    and then a known form carries that form and omitted nothing, and an id that
    stops at the identifier omitted the form. Reading the suffix by a plain
    `endswith` and then deciding omission from the identifier alone answered
    each half without the other, and disagreed with the id in both directions.
    """
    assert circuit_object_id_base(identifier, suffix, existing) == expected


def test_every_form_table_entry_has_a_new_wording_and_vice_versa() -> None:
    assert set(ENTITY_ID_SUFFIX_FORMS) == set(NEW_ENTITY_ID_SUFFIX_WORDS)
    for suffix, words in NEW_ENTITY_ID_SUFFIX_WORDS.items():
        assert words.replace(" ", "_") in ENTITY_ID_SUFFIX_FORMS[suffix], (
            f"the default wording for {suffix} must itself be a known form"
        )


@pytest.mark.parametrize(
    ("identifier", "suffix", "expected"),
    [
        ("Kitchen Outlets", "power", "Kitchen Outlets power"),
        ("Circuit 15", "power", "Circuit 15 power"),
        ("Circuit 15 17", "power", "Circuit 15 17 power"),
        ("Circuit 30 32", "power", "Circuit 30 32 power"),
    ],
)
def test_the_id_shapes_the_upgrade_scenarios_pinned(
    identifier: str, suffix: str, expected: str
) -> None:
    """The four shapes the deleted preset builder's own tests pinned.

    A friendly name, a single-pole breaker and both two-pole spellings. The
    builder produced the whole id and so had to be told the device prefix and
    the naming mode; here the caller has already resolved the identifier and
    only the base is at stake, which is the same string with the device part
    left to Home Assistant.
    """
    assert circuit_object_id_base(identifier, suffix, None) == expected


# --- Releasing the registry name an older release wrote ----------------------

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.naming import release_registry_name_written_by_older_release


def _seed(hass: HomeAssistant, name: str | None) -> str:
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", DOMAIN, "uid-release-test")
    registry.async_update_entity(entry.entity_id, name=name)
    return entry.entity_id


async def test_a_name_an_older_release_wrote_is_released(hass: HomeAssistant) -> None:
    entity_id = _seed(hass, "Kitchen Outlets Consumed Energy")
    registry = er.async_get(hass)

    release_registry_name_written_by_older_release(
        registry, entity_id, "Kitchen Outlets", ("Consumed Energy",)
    )

    assert registry.async_get(entity_id).name is None


async def test_a_name_written_under_a_since_changed_label_is_released_too(
    hass: HomeAssistant,
) -> None:
    """The label was "Energy Consumed" for a few betas; a legacy-names tuple covers it."""
    entity_id = _seed(hass, "Kitchen Outlets Energy Consumed")
    registry = er.async_get(hass)

    release_registry_name_written_by_older_release(
        registry, entity_id, "Kitchen Outlets", ("Consumed Energy", "Energy Consumed")
    )

    assert registry.async_get(entity_id).name is None


async def test_a_name_the_user_set_is_left_alone(hass: HomeAssistant) -> None:
    entity_id = _seed(hass, "Beer Fridge")
    registry = er.async_get(hass)

    release_registry_name_written_by_older_release(
        registry, entity_id, "Kitchen Outlets", ("Consumed Energy",)
    )

    assert registry.async_get(entity_id).name == "Beer Fridge"


async def test_no_name_is_a_no_op(hass: HomeAssistant) -> None:
    entity_id = _seed(hass, None)
    registry = er.async_get(hass)

    release_registry_name_written_by_older_release(
        registry, entity_id, "Kitchen Outlets", ("Consumed Energy",)
    )

    assert registry.async_get(entity_id).name is None


async def test_an_unknown_entity_is_a_no_op(hass: HomeAssistant) -> None:
    registry = er.async_get(hass)
    release_registry_name_written_by_older_release(
        registry, "sensor.does_not_exist", "Kitchen Outlets", ("Consumed Energy",)
    )


from unittest.mock import MagicMock

from custom_components.span_panel.entity import SpanPanelEntity


class _Probe(SpanPanelEntity):
    _attr_name = "Display Name"

    def __init__(self) -> None:
        coordinator = MagicMock()
        super().__init__(coordinator)


def test_the_base_is_what_core_is_told_when_one_is_set() -> None:
    probe = _Probe()
    probe._span_object_id_base = "Circuit 15 power"
    assert probe.suggested_object_id == "Circuit 15 power"


def test_without_a_base_core_gets_the_stock_answer() -> None:
    """Panel, BESS, EVSE and MID entities keep composing from their name."""
    probe = _Probe()
    assert probe.suggested_object_id == "Display Name"
