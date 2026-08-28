"""The suffix mappings are a closed compatibility shim, and this is what closes them.

`get_user_friendly_suffix` and `get_panel_entity_suffix` translate legacy camelCase
description keys into the suffixes their entities have carried since before 2.0.8.
That suffix is shared by the `unique_id` **and** the `entity_id`, so a changed entry
moves both on every installed panel: the `unique_id` costs the long-term statistics,
and the `entity_id` breaks whatever templates and automations a user wrote against it.

Three edits move a live id and all three fail here:

- **adding** a key, which silently reroutes a description that previously resolved
  verbatim -- the failure mode that prompted this file
- **removing** a key, which sends a legacy description back to its raw camelCase
- **changing** a value, which is the most direct version of the same thing

The rule for anything new is verbatim, which is what the sub-device builders have
always done. Nothing here needs extending to add a sensor.
"""

from __future__ import annotations

from custom_components.span_panel.id_builder import (
    ALL_SUFFIX_MAPPINGS,
    CIRCUIT_SUFFIX_MAPPING,
    PANEL_ENTITY_SUFFIX_MAPPING,
    PANEL_SUFFIX_MAPPING,
    build_bess_unique_id,
    build_circuit_unique_id,
    build_evse_unique_id,
    build_mid_unique_id,
    build_panel_unique_id,
    get_panel_entity_suffix,
    get_user_friendly_suffix,
)
from custom_components.span_panel.naming import ENTITY_ID_SUFFIX_FORMS

SERIAL = "sp3-001"
CIRCUIT = "0dad2f16cd514812ae1807b0457d473e"

_CIRCUIT_SUFFIXES = {
    "instantPowerW": "power",
    "producedEnergyWh": "energy_produced",
    "consumedEnergyWh": "energy_consumed",
    "netEnergyWh": "energy_net",
    "importedEnergyWh": "energy_imported",
    "exportedEnergyWh": "energy_exported",
    "circuit_priority": "priority",
    "current": "current",
    "breaker_rating": "breaker_rating",
}

_PANEL_SUFFIXES = {
    "instantGridPowerW": "grid_power",
    "feedthroughPowerW": "feed_through_power",
    "batteryPowerW": "battery_power",
    "pvPowerW": "pv_power",
    "gridPowerFlowW": "grid_power_flow",
    "sitePowerW": "site_power",
    "mainMeterEnergyProducedWh": "main_meter_energy_produced",
    "mainMeterEnergyConsumedWh": "main_meter_energy_consumed",
    "mainMeterNetEnergyWh": "main_meter_energy_net",
    "feedthroughEnergyProducedWh": "feed_through_energy_produced",
    "feedthroughEnergyConsumedWh": "feed_through_energy_consumed",
    "feedthroughNetEnergyWh": "feed_through_energy_net",
    "batteryPercentage": "battery_percentage",
}

_PANEL_ENTITY_SUFFIXES = {
    "instantGridPowerW": "current_power",
    "feedthroughPowerW": "feed_through_power",
    "batteryPowerW": "battery_power",
    "pvPowerW": "pv_power",
    "gridPowerFlowW": "grid_power_flow",
    "sitePowerW": "site_power",
    "mainMeterEnergyProducedWh": "main_meter_produced_energy",
    "mainMeterEnergyConsumedWh": "main_meter_consumed_energy",
    "mainMeterNetEnergyWh": "main_meter_net_energy",
    "feedthroughEnergyProducedWh": "feed_through_produced_energy",
    "feedthroughEnergyConsumedWh": "feed_through_consumed_energy",
    "feedthroughNetEnergyWh": "feed_through_net_energy",
    "batteryPercentage": "battery_level",
}

# The entity-id table, closed for a related but distinct reason. The mappings
# above translate a description key into a canonical suffix; this records every
# *wording* of that suffix an entity id has ever shipped with, so an existing
# entity can be proposed the id it already has. An added entry claims a spelling
# shipped that never did, and Recreate then offers a rename nobody asked for.
_ENTITY_ID_SUFFIX_FORMS = {
    "power": frozenset({"power", "current_power"}),
    "energy_produced": frozenset({"produced_energy", "energy_produced"}),
    "energy_consumed": frozenset({"consumed_energy", "energy_consumed"}),
    "energy_net": frozenset({"net_energy", "energy_net"}),
    "current": frozenset({"current"}),
    "breaker_rating": frozenset({"breaker_rating"}),
    "breaker": frozenset({"breaker"}),
    "circuit_priority": frozenset({"circuit_priority"}),
}

_CLOSED = (
    "This mapping is closed. Adding, removing or changing an entry moves a live "
    "unique_id and entity_id on every installed panel -- statistics and the user's "
    "templates both. A new description key needs no entry: it resolves verbatim."
)

_CLOSED_FORMS = (
    "This table is historical fact, not a style. An entry is added only when "
    "another wording is discovered to have shipped -- never to introduce one, "
    "which would offer every circuit on the panel a rename."
)


def test_the_circuit_suffix_mapping_is_frozen() -> None:
    assert CIRCUIT_SUFFIX_MAPPING == _CIRCUIT_SUFFIXES, _CLOSED


def test_the_panel_suffix_mapping_is_frozen() -> None:
    assert PANEL_SUFFIX_MAPPING == _PANEL_SUFFIXES, _CLOSED


def test_the_panel_entity_suffix_mapping_is_frozen() -> None:
    assert PANEL_ENTITY_SUFFIX_MAPPING == _PANEL_ENTITY_SUFFIXES, _CLOSED


def test_the_entity_id_suffix_forms_are_frozen() -> None:
    """The wordings an entity id has shipped with, which R1 turns on.

    `naming.circuit_object_id_base` reads an existing id's suffix back through
    this table so the entity is proposed its own id. A wording missing from it
    reads as unknown and the entity is offered the new spelling instead; a
    wording invented into it is offered to entities that never had it.
    """
    assert ENTITY_ID_SUFFIX_FORMS == _ENTITY_ID_SUFFIX_FORMS, _CLOSED_FORMS


def test_the_combined_mapping_is_exactly_its_two_halves() -> None:
    """`ALL_SUFFIX_MAPPINGS` is derived, so it cannot gain an entry of its own.

    Pinned because it is the one a caller reaches for, and a hand-added entry here
    would route a key without appearing in either half above.
    """
    assert ALL_SUFFIX_MAPPINGS == {**_CIRCUIT_SUFFIXES, **_PANEL_SUFFIXES}


def test_a_key_the_shim_does_not_carry_resolves_to_itself() -> None:
    """The rule for everything added from here on.

    Sampled from keys real sub-device sensors use, so this fails if the fallback
    is ever changed to normalise, prefix or otherwise reshape an unmapped key.
    """
    for key in ("soe_kwh", "meter_power", "mid_grid_state", "evse_status", "grid_islandable"):
        assert get_user_friendly_suffix(key) == key
        assert get_panel_entity_suffix(key) == key


def test_the_sub_device_builders_never_consult_the_shim() -> None:
    """Verbatim by construction, which is why they are safe from an edit above.

    A BESS, MID or EVSE id is its description key. Even if somebody added a
    mapping entry for one of these keys, these builders would not read it -- and
    the frozen dictionaries above are what stops the attempt reaching review.
    """
    assert build_bess_unique_id(SERIAL, "soe_kwh") == f"span_{SERIAL}_bess_soe_kwh"
    assert build_mid_unique_id(SERIAL, "mid_grid_state") == f"span_{SERIAL}_mid_mid_grid_state"
    assert build_evse_unique_id(SERIAL, "evse-1", "evse_status") == f"span_{SERIAL}_evse_evse-1_evse_status"


def test_the_legacy_ids_the_shim_exists_to_preserve() -> None:
    """What the frozen entries actually buy, spelled out as ids rather than suffixes.

    These are the strings on installed panels. If one of them changes, an upgrade
    orphans the entity, Home Assistant registers a replacement with a `_2` suffix,
    and the history stays on the entity nobody is looking at any more.
    """
    assert build_circuit_unique_id(SERIAL, CIRCUIT, "instantPowerW") == f"span_{SERIAL}_{CIRCUIT}_power"
    assert build_circuit_unique_id(SERIAL, CIRCUIT, "producedEnergyWh") == f"span_{SERIAL}_{CIRCUIT}_energy_produced"
    assert build_circuit_unique_id(SERIAL, "unmapped_tab_32", "instantPowerW") == f"span_{SERIAL}_unmapped_tab_32_power"
    assert build_panel_unique_id(SERIAL, "instantGridPowerW") == f"span_{SERIAL}_current_power"
    assert build_panel_unique_id(SERIAL, "doorState") == f"span_{SERIAL}_doorstate"
