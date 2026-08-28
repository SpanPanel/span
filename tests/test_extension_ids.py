"""The identity grammar for vendor extension properties, and what holds it honest.

The unique id is the one irreversible commitment in extension adoption: nothing
migrates, so an id minted wrong is minted wrong forever. Three properties are
asserted here rather than assumed.

**Injectivity.** Two distinct wire addresses must never produce one id. The
grammar buys this by carrying the wire path verbatim -- the id *is* the address
-- and the adversarial pairs below are the ones any normalising scheme would
collapse.

**Namespace closure.** No curated description key contains the `adopted` token,
so a curated id and an extension id cannot be confused for one another.

**Refusal over sanitisation.** An id outside the Homie charset is refused, not
cleaned up, because cleaning it is what would make the slash-split ambiguous.
"""

from __future__ import annotations

import pytest
from span_panel_api import ExtensionSubject

from custom_components.span_panel.extension import (
    HOMIE_ID,
    extension_scope,
    extension_unique_id,
    is_extension_unique_id,
)
from custom_components.span_panel.field_paths import platform_descriptions
from custom_components.span_panel.util import ADOPTED_IDENTIFIER_TOKEN

SERIAL = "sp3-000000-001"


def _id(kind: str, node: str, prop: str, instance_key: str | None = None) -> str | None:
    return extension_unique_id(
        SERIAL, ExtensionSubject(kind=kind, instance_key=instance_key), node, prop
    )


# --- the grammar ------------------------------------------------------------


def test_the_grammar_is_serial_token_scope_then_the_wire_path() -> None:
    assert (
        _id("battery", "battery-2", "cell-temperature")
        == "span_sp3-000000-001_adopted_bess/battery-2/cell-temperature"
    )


@pytest.mark.parametrize(
    ("kind", "instance_key", "scope"),
    [
        ("panel", None, "panel"),
        ("battery", None, "bess"),
        ("mid", None, "mid"),
        ("pv", None, "pv"),
        ("evse", "acme-001", "evse_acme-001"),
        ("circuit", "0ab966b95f92a6a51ec548485aa85f54", "circuit_0ab966b95f92a6a51ec548485aa85f54"),
    ],
)
def test_each_subject_maps_to_the_scope_its_curated_entities_use(
    kind: str, instance_key: str | None, scope: str
) -> None:
    assert extension_scope(ExtensionSubject(kind=kind, instance_key=instance_key)) == scope


def test_a_multi_instance_subject_without_a_key_names_no_device() -> None:
    """No card to hang it on, so no id: inventing a scope would mint a homeless entity."""
    assert extension_scope(ExtensionSubject(kind="evse")) is None
    assert _id("evse", "acme", "charge-limit") is None


def test_an_unknown_subject_kind_is_refused_rather_than_guessed() -> None:
    assert extension_scope(ExtensionSubject(kind="something-new")) is None


# --- injectivity ------------------------------------------------------------


def test_the_pairs_a_normalising_grammar_would_collapse_stay_distinct() -> None:
    """The concrete counterexample the verbatim rule exists to defeat.

    Any scheme that turned hyphens into underscores and joined the two segments
    with an underscore would render both of these as
    `..._battery_2_cell_temperature`.
    """
    first = _id("battery", "battery-2", "cell-temperature")
    second = _id("battery", "battery", "2-cell-temperature")
    assert first is not None
    assert second is not None
    assert first != second


def test_ids_are_injective_over_adversarial_addresses() -> None:
    """Distinct addresses, distinct ids -- asserted over a set built to collide."""
    addresses = [
        ("battery-2", "cell-temperature"),
        ("battery", "2-cell-temperature"),
        ("battery-2-cell", "temperature"),
        ("meter", "acme-cell-balance"),
        ("meter-acme", "cell-balance"),
        ("a", "b-c"),
        ("a-b", "c"),
    ]
    minted: dict[str, tuple[str, str]] = {}
    for node, prop in addresses:
        unique_id = _id("battery", node, prop)
        assert unique_id is not None, (node, prop)
        assert unique_id not in minted, f"{(node, prop)} collides with {minted[unique_id]}"
        minted[unique_id] = (node, prop)


def test_the_same_address_on_two_instances_stays_distinct() -> None:
    """Two chargers publishing the same vendor node are two entities, not one."""
    first = _id("evse", "acme", "cell-temperature", instance_key="acme-001")
    second = _id("evse", "acme", "cell-temperature", instance_key="acme-002")
    assert first != second


# --- namespace closure ------------------------------------------------------


def test_no_curated_description_key_contains_the_adopted_token() -> None:
    """The namespace cannot be invaded silently by a curated key.

    The same closed-mapping idiom the suffix tests use: asserted over every
    platform description rather than over a hand-listed sample.
    """
    invaded = [
        description.key
        for description in platform_descriptions()
        if ADOPTED_IDENTIFIER_TOKEN in description.key.lower()
    ]
    assert not invaded, f"curated keys carrying the adoption token: {invaded}"


def test_an_extension_id_is_distinguishable_from_a_device_adoption_id() -> None:
    """The slash is the discriminator; device-level adoption ids carry none."""
    extension = _id("battery", "battery-2", "cell-temperature")
    assert extension is not None
    assert is_extension_unique_id(extension)

    # The device-level grammar, as `adoption.adopted_unique_id` builds it.
    device_level = f"span_{SERIAL}_{ADOPTED_IDENTIFIER_TOKEN}_acme-001_meter_active_power"
    assert not is_extension_unique_id(device_level)

    curated = f"span_{SERIAL}_bess_battery_power"
    assert not is_extension_unique_id(curated)


# --- refusal over sanitisation ----------------------------------------------


@pytest.mark.parametrize(
    "node",
    ["Battery-2", "battery_2", "battery/2", "battery 2", "battery.2", ""],
)
def test_an_off_charset_node_is_refused(node: str) -> None:
    assert _id("battery", node, "cell-temperature") is None


@pytest.mark.parametrize(
    "prop",
    ["Cell-Temperature", "cell_temperature", "cell/temperature", "cell temperature", ""],
)
def test_an_off_charset_property_is_refused(prop: str) -> None:
    assert _id("battery", "battery-2", prop) is None


def test_the_charset_is_exactly_the_homie_one() -> None:
    """Lowercase alphanumerics and hyphens, and nothing that could break the split."""
    assert HOMIE_ID.match("battery-2")
    assert HOMIE_ID.match("a1")
    assert not HOMIE_ID.match("A")
    assert not HOMIE_ID.match("a_b")
    assert not HOMIE_ID.match("a/b")
