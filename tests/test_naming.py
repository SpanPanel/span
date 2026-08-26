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


# --- The id shape kept where composition would move one --------------------
#
# Two kinds of circuit entity are spelled differently by Home Assistant's
# composition than by the builder that preset their ids: one on a sub-device,
# where the DEVICE part is the charger and not the panel, and one on an install
# that turned the device prefix off, where composition prefixes anyway. Neither
# difference is anything the user did, so those entities keep being preset.

from custom_components.span_panel.naming import (
    LEGACY_PRESET_DEVICE_NAME,
    legacy_preset_entity_id,
    legacy_preset_for_existing,
)


def test_a_sub_device_sensor_keeps_the_panel_prefixed_id_it_has() -> None:
    """Composition would spell this one with the charger's name; its id says the panel."""
    assert (
        legacy_preset_entity_id(
            "sensor",
            LEGACY_PRESET_DEVICE_NAME,
            "Kitchen Outlets",
            "power",
            "sensor.span_panel_kitchen_outlets_power",
        )
        == "sensor.span_panel_kitchen_outlets_power"
    )


def test_an_install_without_the_device_prefix_keeps_its_bare_id() -> None:
    """`has_entity_name` prefixes the device unconditionally; these ids never did."""
    assert (
        legacy_preset_entity_id(
            "sensor", None, "Kitchen Outlets", "power", "sensor.kitchen_outlets_power"
        )
        == "sensor.kitchen_outlets_power"
    )


def test_a_renamed_circuit_still_refreshes_the_name_half() -> None:
    """Issue #252 has to survive the preset: the id is computed, never read back."""
    assert (
        legacy_preset_entity_id(
            "sensor",
            LEGACY_PRESET_DEVICE_NAME,
            "Beer Fridge",
            "power",
            "sensor.span_panel_kitchen_outlets_power",
        )
        == "sensor.span_panel_beer_fridge_power"
    )
    assert (
        legacy_preset_entity_id(
            "sensor", None, "Beer Fridge", "power", "sensor.kitchen_outlets_power"
        )
        == "sensor.beer_fridge_power"
    )


@pytest.mark.parametrize(
    ("device_slug", "existing", "expected"),
    [
        (
            LEGACY_PRESET_DEVICE_NAME,
            "sensor.span_panel_kitchen_consumed_energy",
            "sensor.span_panel_kitchen_consumed_energy",
        ),
        (
            LEGACY_PRESET_DEVICE_NAME,
            "sensor.span_panel_kitchen_energy_consumed",
            "sensor.span_panel_kitchen_energy_consumed",
        ),
        (None, "sensor.kitchen_consumed_energy", "sensor.kitchen_consumed_energy"),
        (None, "sensor.kitchen_energy_consumed", "sensor.kitchen_energy_consumed"),
    ],
)
def test_the_suffix_words_come_from_the_id_the_entity_already_has(
    device_slug: str | None, existing: str, expected: str
) -> None:
    """Both spellings this integration has shipped survive the preset, as they must."""
    assert (
        legacy_preset_entity_id("sensor", device_slug, "Kitchen", "energy_consumed", existing)
        == expected
    )


def test_the_preset_device_name_is_the_literal_the_old_builder_fell_back_to() -> None:
    """Not the panel's own name: the preset builder passed none.

    A second panel on a system is named "Span Panel 2" and its circuit ids still
    began `span_panel_`, so the panel's name is the wrong thing to spell an
    existing id with.
    """
    assert LEGACY_PRESET_DEVICE_NAME == "Span Panel"


# --- Who is offered the kept shape, and who composes -------------------------
#
# The same decision for every platform: the sensors, the breaker switch and the
# priority select all ask this one question rather than each keeping a copy of
# the answer. A copy is how three platforms drift apart.


def test_a_new_entity_composes_whatever_the_install_looks_like() -> None:
    """There is no id to protect, so nothing is kept and Core composes."""
    assert (
        legacy_preset_for_existing(
            "switch",
            identifier="Kitchen Outlets",
            suffix="breaker",
            existing_entity_id=None,
            use_device_prefix=False,
            is_sub_device=True,
        )
        is None
    )


def test_an_ordinary_existing_entity_composes_too() -> None:
    """The device prefix is on and the entity is on the panel: composition agrees."""
    assert (
        legacy_preset_for_existing(
            "switch",
            identifier="Kitchen Outlets",
            suffix="breaker",
            existing_entity_id="switch.span_panel_kitchen_outlets_breaker",
            use_device_prefix=True,
            is_sub_device=False,
        )
        is None
    )


def test_an_existing_entity_on_a_no_prefix_install_keeps_its_bare_id() -> None:
    """`has_entity_name` prefixes the device unconditionally; these ids never did."""
    assert (
        legacy_preset_for_existing(
            "select",
            identifier="Kitchen Outlets",
            suffix="circuit_priority",
            existing_entity_id="select.kitchen_outlets_circuit_priority",
            use_device_prefix=False,
            is_sub_device=False,
        )
        == "select.kitchen_outlets_circuit_priority"
    )


def test_an_existing_sub_device_entity_keeps_the_panel_prefix() -> None:
    """Composition would name the charger; the id names the panel and goes on doing so."""
    assert (
        legacy_preset_for_existing(
            "sensor",
            identifier="Kitchen Outlets",
            suffix="power",
            existing_entity_id="sensor.span_panel_kitchen_outlets_power",
            use_device_prefix=True,
            is_sub_device=True,
        )
        == "sensor.span_panel_kitchen_outlets_power"
    )


def test_the_kept_id_still_follows_a_circuit_rename() -> None:
    """Computed from current panel data, so #252 survives the exception."""
    assert (
        legacy_preset_for_existing(
            "switch",
            identifier="Beer Fridge",
            suffix="breaker",
            existing_entity_id="switch.kitchen_outlets_breaker",
            use_device_prefix=False,
            is_sub_device=False,
        )
        == "switch.beer_fridge_breaker"
    )


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
