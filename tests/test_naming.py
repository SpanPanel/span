"""The base Home Assistant composes a circuit entity's id from."""

from __future__ import annotations

from homeassistant.util import slugify
import pytest

from custom_components.span_panel.naming import (
    ENTITY_ID_SUFFIX_FORMS,
    NEW_ENTITY_ID_SUFFIX_WORDS,
    circuit_object_id_base,
    circuit_object_id_suffix,
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


# --- The suffix alone, for a circuit entity on its circuit's device ----------
#
# The cases above, each without its identifier. Every port passes the
# identifier as the device name too -- an install whose circuit device is named
# what its ids were built from -- so each answers exactly what the base did,
# minus the name half. The cases after them pin the suffix's own contract,
# including where the device name and the identifier differ.


@pytest.mark.parametrize(
    ("identifier", "suffix", "expected"),
    [
        ("Circuit 15", "power", "power"),
        ("Circuit 30 32", "energy_consumed", "consumed energy"),
        ("Kitchen Outlets", "energy_produced", "produced energy"),
        ("Kitchen Outlets", "energy_net", "net energy"),
        ("Kitchen Outlets", "current", "current"),
        ("Kitchen Outlets", "breaker_rating", "breaker rating"),
        ("Kitchen Outlets", "breaker", "breaker"),
        ("Kitchen Outlets", "circuit_priority", "circuit priority"),
    ],
)
def test_a_new_entity_gets_the_noun_last_suffix(
    identifier: str, suffix: str, expected: str
) -> None:
    assert circuit_object_id_suffix(identifier, identifier, suffix, None) == expected


@pytest.mark.parametrize(
    ("existing", "suffix", "expected"),
    [
        # Preset-era spelling is kept, so Recreate proposes the entity its own id.
        ("sensor.span_panel_circuit_15_energy_consumed", "energy_consumed", "energy consumed"),
        ("sensor.span_panel_circuit_15_energy_produced", "energy_produced", "energy produced"),
        ("sensor.span_panel_circuit_15_energy_net", "energy_net", "energy net"),
        # Pre-preset spelling is kept too.
        ("sensor.span_panel_circuit_15_consumed_energy", "energy_consumed", "consumed energy"),
        # The oldest power spelling.
        ("sensor.span_panel_circuit_15_current_power", "power", "current power"),
        # A suffix with one form only.
        ("switch.span_panel_circuit_15_breaker", "breaker", "breaker"),
    ],
)
def test_an_existing_entity_keeps_the_suffix_form_it_has(
    existing: str, suffix: str, expected: str
) -> None:
    assert circuit_object_id_suffix("Circuit 15", "Circuit 15", suffix, existing) == expected


def test_an_existing_id_matching_no_known_form_gets_the_default_suffix() -> None:
    assert (
        circuit_object_id_suffix(
            "Circuit 15", "Circuit 15", "energy_consumed", "sensor.something_else"
        )
        == "consumed energy"
    )


def test_the_suffix_is_omitted_when_the_name_already_ends_with_it() -> None:
    """Preserves the preset builder's rule: a circuit named "Solar Power" got `..._solar_power`."""
    assert circuit_object_id_suffix("Solar Power", "Solar Power", "power", None) == ""
    assert (
        circuit_object_id_suffix(
            "Solar Power", "Solar Power", "power", "sensor.span_panel_solar_power"
        )
        == ""
    )


def test_a_name_that_is_only_the_suffix_word_keeps_the_suffix() -> None:
    """The builder's test was `endswith(f"_{suffix}")`, underscore included.

    A circuit named exactly "Power" therefore got `..._power_power`, because
    "power" does not end with "_power" and there was nothing to omit. Omitting
    it here would offer every such circuit a rename to `..._power` -- a move the
    user did not cause, which R1 forbids.
    """
    assert circuit_object_id_suffix("Power", "Power", "power", None) == "power"
    assert (
        circuit_object_id_suffix("Power", "Power", "power", "sensor.span_panel_power_power")
        == "power"
    )


def test_a_renamed_circuit_reads_the_suffix_from_the_end_of_its_id() -> None:
    """Issue #252 at the layer that decides it: only the name half follows the panel.

    The id still names the circuit's old name, so it no longer names the
    identifier; it is consulted for nothing but which spelling of the suffix it
    shipped with, read from its end.
    """
    assert (
        circuit_object_id_suffix(
            "Kitchen", "Kitchen", "power", "sensor.span_panel_kitchen_outlets_power"
        )
        == "power"
    )


@pytest.mark.parametrize(
    ("identifier", "suffix", "existing", "expected"),
    [
        # A circuit named "Current". The id's trailing `_current_power` is the
        # identifier followed by `power`, not the `current_power` spelling of the
        # suffix -- reading it as the latter proposed `..._current_current_power`.
        ("Current", "power", "sensor.span_panel_current_power", "power"),
        # A circuit named "Solar Power" whose id kept both halves. The omission
        # rule looks only at the name, so it proposed `..._solar_power` and
        # offered every such circuit a rename.
        ("Solar Power", "power", "sensor.span_panel_solar_power_power", "power"),
        # The same circuit on an install whose id did omit them.
        ("Solar Power", "power", "sensor.span_panel_solar_power", ""),
        # Both, on an install with no device prefix: the identifier opens the
        # object id rather than following an underscore.
        ("Solar Power", "power", "sensor.solar_power_power", "power"),
        ("Solar Power", "power", "sensor.solar_power", ""),
        # A circuit named exactly "Power", which never omitted anything.
        ("Power", "power", "sensor.span_panel_power_power", "power"),
    ],
)
def test_what_follows_the_identifier_settles_the_suffix(
    identifier: str, suffix: str, existing: str, expected: str
) -> None:
    """Where the id names this circuit, what follows the name settles both halves.

    Which form the id carries, and whether the preset builder omitted it: an id
    that spells the identifier and then a known form carries that form, and an
    id that stops at the identifier omitted it.
    """
    assert circuit_object_id_suffix(identifier, identifier, suffix, existing) == expected


@pytest.mark.parametrize(
    "identifier", ["Kitchen Outlets", "Circuit 15", "Circuit 15 17", "Circuit 30 32"]
)
def test_the_upgrade_scenario_shapes_keep_the_suffix(identifier: str) -> None:
    """The four shapes the deleted preset builder's own tests pinned.

    A friendly name, a single-pole breaker and both two-pole spellings. None of
    them ends on a suffix word, so each keeps `power` whole.
    """
    assert circuit_object_id_suffix(identifier, identifier, "power", None) == "power"


def test_suffix_is_empty_when_the_id_ends_on_the_circuit_name() -> None:
    """Core then composes the id from the devices alone, and it is the id the entity has."""
    assert (
        circuit_object_id_suffix(
            "Solar Power", "Solar Power", "power", "sensor.span_panel_solar_power"
        )
        == ""
    )


def test_numbers_mode_reads_the_form_after_the_tabs() -> None:
    """The id was built from the tabs, so the tabs -- not the device name -- locate the form."""
    assert (
        circuit_object_id_suffix(
            "Circuit 15",
            "Kitchen Outlets",
            "energy_consumed",
            "sensor.span_panel_circuit_15_energy_consumed",
        )
        == "energy consumed"
    )


def test_new_entity_gets_noun_last_words() -> None:
    assert (
        circuit_object_id_suffix("Kitchen Outlets", "Kitchen Outlets", "energy_consumed", None)
        == "consumed energy"
    )


def test_omission_follows_the_device_name() -> None:
    """With no id to read back, the device Core composes from decides the omission.

    A numbers-mode circuit named "Solar Power" sits on a device of that name, so
    the suffix would repeat what the device part already says.
    """
    assert circuit_object_id_suffix("Circuit 15", "Solar Power", "power", None) == ""


@pytest.mark.parametrize(
    ("identifier", "device_name", "existing", "expected"),
    [
        # The device's name ends on the suffix word, but the id kept the suffix.
        ("Circuit 15", "Solar Power", "sensor.span_panel_circuit_15_power", "power"),
        # The device's name does not, but the id stops at the identifier.
        ("Solar Power", "Roof Array", "sensor.span_panel_solar_power", ""),
    ],
)
def test_an_id_that_names_the_identifier_decides_the_omission_over_the_device_name(
    identifier: str, device_name: str, existing: str, expected: str
) -> None:
    """The device name's ending decides the omission only where the id gives no answer."""
    assert circuit_object_id_suffix(identifier, device_name, "power", existing) == expected


@pytest.mark.parametrize(
    "id_identifier",
    [pytest.param("Solar Power", id="names"), pytest.param("Circuit 15", id="numbers")],
)
def test_a_renamed_circuit_omits_the_suffix_its_new_device_name_ends_on(
    id_identifier: str,
) -> None:
    """Issue #252 with an id to read: the id settles the form, the device name the omission.

    The id names the circuit's old name, so it no longer answers whether the
    form was left out; the device the id is composed from now does.
    """
    assert (
        circuit_object_id_suffix(
            id_identifier, "Solar Power", "power", "sensor.span_panel_kitchen_outlets_power"
        )
        == ""
    )


def _id_core_composes(domain: str, device_name: str, base: str) -> str:
    """Return the id Core composes by default: the panel, the circuit's device, the base."""
    parts = ("Span Panel", device_name, base)
    return f"{domain}.{slugify(' '.join(part for part in parts if part))}"


@pytest.mark.parametrize(
    ("device_name", "domain", "suffix", "form_words"),
    [
        ("Solar Power", "sensor", "power", "power"),
        ("Current", "sensor", "power", "power"),
        ("Main Breaker", "switch", "breaker", "breaker"),
        ("Kitchen Outlets", "sensor", "energy_consumed", "energy consumed"),
    ],
)
@pytest.mark.parametrize(
    "id_identifier",
    # None is names mode, where the identifier is the circuit's name, as the device's is.
    [pytest.param(None, id="names"), pytest.param("Circuit 15", id="numbers")],
)
@pytest.mark.parametrize(
    "named_before",
    [
        pytest.param(None, id="new"),
        # The circuit-numbers preset, or a circuit unnamed until now.
        pytest.param("Circuit 15", id="tabs-built"),
        # A circuit renamed on the panel since (#252).
        pytest.param("Old Name", id="renamed"),
    ],
)
def test_an_accepted_offer_reads_back_to_the_same_suffix(
    device_name: str,
    domain: str,
    suffix: str,
    form_words: str,
    id_identifier: str | None,
    named_before: str | None,
) -> None:
    """Recreate is idempotent: the id it offers, once accepted, offers nothing more.

    The existing id is read from the registry each time the entity is built, so
    the restart after the user accepts an offer hands this function the id Core
    just composed from it. Any other answer then is a second rename the user did
    not cause (R1) -- `..._current_power` offered `..._current_current_power`,
    and `..._solar_power_power` offered `..._solar_power`.
    """
    identifier = device_name if id_identifier is None else id_identifier
    existing = None if named_before is None else _id_core_composes(domain, named_before, form_words)

    offered = circuit_object_id_suffix(identifier, device_name, suffix, existing)
    accepted = _id_core_composes(domain, device_name, offered)

    assert circuit_object_id_suffix(identifier, device_name, suffix, accepted) == offered


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
