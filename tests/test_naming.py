"""The base Home Assistant composes a circuit entity's id from."""

from __future__ import annotations

from homeassistant.util import slugify
import pytest

from custom_components.span_panel.naming import (
    ENTITY_ID_SUFFIX_FORMS,
    NEW_ENTITY_ID_SUFFIX_WORDS,
    circuit_object_id_suffix,
)


def test_every_form_table_entry_has_a_new_wording_and_vice_versa() -> None:
    assert set(ENTITY_ID_SUFFIX_FORMS) == set(NEW_ENTITY_ID_SUFFIX_WORDS)
    for suffix, words in NEW_ENTITY_ID_SUFFIX_WORDS.items():
        assert words.replace(" ", "_") in ENTITY_ID_SUFFIX_FORMS[suffix], (
            f"the default wording for {suffix} must itself be a known form"
        )


# --- The suffix alone, for a circuit entity on its circuit's device ----------
#
# The first cases pass the identifier as the device name too -- an install
# whose circuit device is named what its ids were built from -- and pin the
# read-back the base has always made, without the name half the device now
# carries. The cases after them pin the suffix's own contract where the device
# name and the identifier differ.


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


def test_an_unnamed_charger_circuit_keeps_its_suffix_under_a_device_named_by_its_tabs() -> None:
    """The rules' row for an unnamed charger circuit that feeds no charger device.

    Its sensors' ids say "EV Charger", the name those sensors had, and its
    device is named by its tabs, as its switch and select always were. The id
    names the identifier, so what follows it settles the form, and in
    circuit-names mode Recreate offers the tabs' id with the suffix the id
    already carries: `sensor.span_panel_circuit_7_power`.
    """
    assert (
        circuit_object_id_suffix(
            "EV Charger", "Circuit 7", "power", "sensor.span_panel_ev_charger_power"
        )
        == "power"
    )
    assert (
        circuit_object_id_suffix(
            "EV Charger",
            "Circuit 7",
            "energy_consumed",
            "sensor.span_panel_ev_charger_energy_consumed",
        )
        == "energy consumed"
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
    ("identifier", "device_name", "suffix", "existing", "expected"),
    [
        # Circuit-numbers presets on a device whose name ends on the suffix
        # word: the id the circuit has under circuit names leaves the word out.
        ("Circuit 15", "Solar Power", "power", "sensor.span_panel_circuit_15_power", ""),
        ("Circuit 15", "Main Breaker", "breaker", "switch.span_panel_circuit_15_breaker", ""),
        # The device's name does not end on it, so the suffix stays, in the form
        # the id spells after the tabs.
        ("Circuit 15", "Kitchen Outlets", "power", "sensor.span_panel_circuit_15_power", "power"),
        (
            "Circuit 15",
            "Kitchen Outlets",
            "power",
            "sensor.span_panel_circuit_15_current_power",
            "current power",
        ),
        # The id stops at the identifier, which is not what the device is
        # called: the stop was the identifier's omission, not this device's.
        ("Solar Power", "Roof Array", "power", "sensor.span_panel_solar_power", "power"),
        ("Circuit 15", "Kitchen Outlets", "power", "sensor.span_panel_circuit_15", "power"),
    ],
)
def test_an_id_built_from_the_identifier_takes_its_omission_from_the_device_name(
    identifier: str, device_name: str, suffix: str, existing: str, expected: str
) -> None:
    """The id settles the form; the device Core composes from settles the omission.

    An id built from the tabs, or from a name the device no longer carries,
    says which spelling of the suffix the entity shipped with. Whether the
    suffix is left out is a question about the name in front of it, and the
    offer is composed from the device's, so the answer is the one a new entity
    on that device gets. In circuit-numbers mode that makes the first offer the
    id the circuit has under circuit names: `..._solar_power`, not
    `..._solar_power_power`.
    """
    assert circuit_object_id_suffix(identifier, device_name, suffix, existing) == expected


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


@pytest.mark.parametrize(
    ("id_identifier", "device_name", "suffix", "existing"),
    [
        # Circuit-numbers presets whose circuit device is named the suffix word.
        ("Circuit 15", "Power", "power", "sensor.span_panel_circuit_15_power"),
        ("Circuit 15", "Current", "current", "sensor.span_panel_circuit_15_current"),
        ("Circuit 15", "Breaker", "breaker", "switch.span_panel_circuit_15_breaker"),
        # Names mode, with the circuit's device renamed "Power" by the user.
        ("Kitchen Outlets", "Power", "power", "sensor.span_panel_kitchen_outlets_power"),
    ],
)
def test_a_device_named_the_suffix_word_does_not_swallow_the_suffix(
    id_identifier: str, device_name: str, suffix: str, existing: str
) -> None:
    """The longer reading of the id wins, so the suffix stays whole, as the underscore rule keeps it.

    The trailing `power` of `..._circuit_15_power` also reads as an id that
    stops at a device named "Power". The tabs account for `circuit_15_power`,
    the device for `power` alone, so the id is read as the tabs then `power`:
    the suffix a new entity on that device gets too.
    """
    assert circuit_object_id_suffix(id_identifier, device_name, suffix, existing) == suffix
    assert circuit_object_id_suffix(id_identifier, device_name, suffix, None) == suffix


def test_the_device_wins_when_both_readings_account_for_the_same_tail() -> None:
    """`..._solar_power` reads as device "Solar Power" stopping, or as circuit "Solar" then `power`.

    The two are the same characters, and the device's reading reproduces the
    id from the name Core composes it from now, so Recreate offers nothing.
    """
    assert (
        circuit_object_id_suffix("Solar", "Solar Power", "power", "sensor.span_panel_solar_power")
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
        # Devices named the suffix word itself.
        ("Power", "sensor", "power", "power"),
        ("Current", "sensor", "current", "current"),
        ("Breaker", "switch", "breaker", "breaker"),
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


# --- Trimming a leading circuit name from a name the user set ----------------
#
# Once a circuit has a device named after it, Home Assistant puts that name in
# front of a name the user gave one of its entities, so a name that already
# began with it would show it twice. The conversion to circuit devices trims
# it, by the rule Home Assistant's own migration used for device names.

from homeassistant.helpers.entity_registry import (
    _async_strip_prefix_from_entity_name as core_strip_prefix,
)

from custom_components.span_panel.naming import trim_leading_circuit_name

CIRCUIT = "Kitchen Outlets"


def test_a_leading_circuit_name_is_trimmed() -> None:
    assert trim_leading_circuit_name("Kitchen Outlets Power usage", CIRCUIT) == "Power usage"


def test_the_trim_ignores_case_takes_the_separator_and_capitalizes() -> None:
    assert trim_leading_circuit_name("kitchen outlets: power usage", CIRCUIT) == "Power usage"


def test_a_name_that_is_the_circuit_name_trims_to_nothing() -> None:
    """Empty, not None: the entity is then named by its device alone, as it was."""
    assert trim_leading_circuit_name("Kitchen Outlets", CIRCUIT) == ""


def test_a_name_the_circuit_name_runs_into_is_not_trimmed() -> None:
    """No separator follows the circuit's name, so it is not a prefix but part of a word."""
    assert trim_leading_circuit_name("Kitchen OutletsPower", CIRCUIT) is None


def test_a_name_that_does_not_start_with_the_circuit_name_is_not_trimmed() -> None:
    assert trim_leading_circuit_name("Fridge Power", CIRCUIT) is None


@pytest.mark.parametrize(
    ("name", "circuit_name"),
    [
        ("Kitchen Outlets Power usage", "Kitchen Outlets"),
        ("kitchen outlets: power usage", "Kitchen Outlets"),
        ("KITCHEN OUTLETS - iPhone charger", "Kitchen Outlets"),
        ("Kitchen Outlets -: ", "Kitchen Outlets"),
        ("Kitchen Outlets", "Kitchen Outlets"),
        ("Kitchen OutletsPower", "Kitchen Outlets"),
        ("Kitchen", "Kitchen Outlets"),
        ("Fridge Power", "Kitchen Outlets"),
        ("Stra\u00dfe Licht", "STRASSE"),
        ("STRASSE Licht", "Stra\u00dfe"),
        ("\u0130stanbul lights", "i\u0307stanbul"),
        ("Stra\u00dfe Licht", "Stras"),
        ("", "Kitchen Outlets"),
        ("Kitchen Outlets Power", ""),
    ],
)
def test_the_trim_is_core_s_own_rule(name: str, circuit_name: str) -> None:
    """The copy answers as Home Assistant's private function does, case folding included.

    Core's function is private, so the integration carries the rule. This holds
    the copy to the original on the Home Assistant the suite runs on, so a
    change to Core's rule is seen here and not as two names that disagree.
    """
    assert trim_leading_circuit_name(name, circuit_name) == core_strip_prefix(name, circuit_name)


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
