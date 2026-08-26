"""Devices this integration models nothing for surface without disturbing curated ones.

Three properties carry the whole design and each has a test here that fails if it
stops holding: nothing adopted enters long-term statistics, an adopted device
keeps the identity it was first seen under, and the notice counts adopted devices
rather than listing their entities.
"""

from __future__ import annotations

import ast
from dataclasses import replace
import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import AdoptedDevice, AdoptedProperty, PublishOutcome, PublishState
from span_panel_api.exceptions import SpanPanelServerError

from custom_components.span_panel.adoption import (
    CONTROL_PLATFORMS,
    DEVICE_CLASS_BY_UNIT,
    adopted_control_count,
    adopted_identifier,
    adopted_unique_id,
    async_register_adopted_devices,
    classify,
    create_adopted_binary_sensors,
    create_adopted_numbers,
    create_adopted_selects,
    create_adopted_sensors,
    create_adopted_switches,
    resolve_identifier,
)
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.diagnostics import _adoption
from custom_components.span_panel.id_builder import build_panel_unique_id
from custom_components.span_panel.util import (
    ADOPTED_IDENTIFIER_TOKEN,
    classify_sub_device_identifier,
)

from .factories import SpanPanelSnapshotFactory

if TYPE_CHECKING:
    from span_panel_api import SpanPanelSnapshot

PANEL_SERIAL = "sp3-242424-001"


@pytest.fixture
def registered_panel(hass: HomeAssistant) -> tuple[str, str]:
    """Return a config entry and a registered panel device, as setup would leave them.

    Both are required rather than convenient: the device registry refuses to link
    a device to an unknown config entry, and refuses a `via_device_id` naming a
    device that does not exist. An adopted device is a sub-device of the panel, so
    the panel has to be there first -- which is exactly why registration runs
    after `ensure_device_registered` and before the platforms.
    """
    mock = MockConfigEntry(domain=DOMAIN, data={}, unique_id=PANEL_SERIAL)
    mock.add_to_hass(hass)
    panel = dr.async_get(hass).async_get_or_create(
        config_entry_id=mock.entry_id,
        identifiers={(DOMAIN, PANEL_SERIAL)},
        name="Span Panel",
    )
    return str(mock.entry_id), panel.id


def _property(
    node_id: str = "meter",
    property_id: str = "active-power",
    datatype: str = "float",
    unit: str | None = "W",
    fmt: str | None = None,
    settable: bool = False,
    value: str | None = "2400",
) -> AdoptedProperty:
    return AdoptedProperty(
        node_id=node_id,
        property_id=property_id,
        datatype=datatype,
        unit=unit,
        format=fmt,
        settable=settable,
        value=value,
    )


def _device(
    device_id: str = "generator-1",
    *,
    serial_number: str | None = None,
    properties: tuple[AdoptedProperty, ...] = (),
) -> AdoptedDevice:
    return AdoptedDevice(
        device_id=device_id,
        device_type="energy.ebus.device.generator",
        name="Backup Generator",
        model="GEN-9000",
        serial_number=serial_number,
        properties=properties,
    )


def _snapshot(*devices: AdoptedDevice) -> SpanPanelSnapshot:
    """Return a complete snapshot carrying the given adopted devices.

    Built through `replace` rather than by teaching the factory a keyword, so the
    factory keeps describing a curated panel and adoption stays visibly additive.
    """
    return replace(SpanPanelSnapshotFactory.create_complete(serial_number=PANEL_SERIAL), adopted_devices=devices)


# -- The platform table ------------------------------------------------------


@pytest.mark.parametrize(
    ("datatype", "settable", "fmt", "expected"),
    [
        ("boolean", True, None, Platform.SWITCH),
        ("boolean", False, None, Platform.BINARY_SENSOR),
        ("enum", True, "AUTO,MANUAL,OFF", Platform.SELECT),
        ("float", True, "0:100:1", Platform.NUMBER),
        ("integer", True, "0:100:1", Platform.NUMBER),
        ("float", False, None, Platform.SENSOR),
        ("enum", False, "AUTO,MANUAL", Platform.SENSOR),
        ("string", True, None, Platform.SENSOR),
    ],
)
def test_the_declaration_decides_the_platform(
    datatype: str, settable: bool, fmt: str | None, expected: Platform
) -> None:
    """The rule in one table, including its two fallbacks."""
    assert classify(_property(datatype=datatype, fmt=fmt, settable=settable, unit=None)) is expected


@pytest.mark.parametrize("datatype", ["enum", "float", "integer"])
def test_a_settable_property_with_no_value_domain_falls_back_to_a_reading(datatype: str) -> None:
    """Not caution -- the absence of the thing a control is made of.

    `format` is where Homie carries the domain. A select with no option list and
    a number with no bounds are not safer controls, they are broken ones, so the
    property surfaces as the reading it can still be.
    """
    assert classify(_property(datatype=datatype, settable=True, fmt=None, unit=None)) is Platform.SENSOR


def test_a_settable_boolean_needs_no_format_because_its_domain_is_the_datatype() -> None:
    """The one control whose value domain the datatype already states in full."""
    assert classify(_property(datatype="boolean", settable=True, fmt=None, unit=None)) is Platform.SWITCH


# -- Nothing adopted enters long-term statistics -----------------------------


def test_no_adopted_sensor_carries_a_state_class(hass: HomeAssistant) -> None:
    """The single most important assertion in this module.

    `state_class` is not declared on the wire and is not derivable from one: this
    integration ships `feedthroughEnergyProducedWh` as TOTAL beside
    `mainMeterEnergyProducedWh` as TOTAL_INCREASING, same unit and same device
    class. A wrong one writes corrupt long-term statistics that fixing the
    producer afterwards does not repair, so adoption classifies none of them --
    and a user who wants statistics from an adopted reading wraps it themselves.
    """
    declarations = tuple(
        _property(property_id=name, unit=unit, datatype="float")
        for name, unit in (("active-power", "W"), ("imported-energy", "Wh"), ("exported-energy", "kWh"))
    )
    entities = create_adopted_sensors(
        MagicMock(data=_snapshot(_device(properties=declarations))),
        _snapshot(_device(properties=declarations)),
        dr.async_get(hass),
        panel_device_id="panel-device-id",
    )

    assert len(entities) == 3
    assert all(entity.state_class is None for entity in entities)


def test_no_state_class_is_set_anywhere_in_the_module() -> None:
    """The rule stated once more, against the syntax rather than an instance.

    An instance test only covers the paths a test constructs, so a future branch
    that set a state class on some platform nobody instantiated here would pass
    every test above. Read as syntax rather than as text because the module's own
    prose has to be free to explain why the rule exists.
    """
    from custom_components.span_panel import adoption

    tree = ast.parse(Path(adoption.__file__).read_text(encoding="utf-8"))
    keywords = [node.arg for node in ast.walk(tree) if isinstance(node, ast.keyword)]
    targets = [node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)]
    assert "state_class" not in keywords
    assert "_attr_state_class" not in targets
    assert not [node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and "StateClass" in node.id]


# -- Device class is enumerated, never inferred ------------------------------


def test_a_declared_unit_this_integration_knows_gets_a_device_class(hass: HomeAssistant) -> None:
    declarations = (_property(unit="W"),)
    (entity,) = create_adopted_sensors(
        MagicMock(data=_snapshot(_device(properties=declarations))),
        _snapshot(_device(properties=declarations)),
        dr.async_get(hass),
        panel_device_id="panel-device-id",
    )
    assert entity.device_class is SensorDeviceClass.POWER


@pytest.mark.parametrize("unit", ["%", "ppm", "kg"])
def test_a_unit_outside_the_map_gets_no_device_class(hass: HomeAssistant, unit: str) -> None:
    """An unlabelled reading is honest; a mislabelled one is not.

    `%` is the case that makes the rule earn its keep. Its uses in this
    vocabulary are not one class -- a state of charge, a confidence, a duty cycle
    -- so a rule that guessed BATTERY for all of them would mislabel most.
    """
    declarations = (_property(unit=unit),)
    (entity,) = create_adopted_sensors(
        MagicMock(data=_snapshot(_device(properties=declarations))),
        _snapshot(_device(properties=declarations)),
        dr.async_get(hass),
        panel_device_id="panel-device-id",
    )
    assert entity.device_class is None
    assert unit not in DEVICE_CLASS_BY_UNIT


# -- Everything adopted is disabled and diagnostic ---------------------------


def test_every_adopted_entity_is_disabled_and_diagnostic(hass: HomeAssistant) -> None:
    """Adoption makes a device reachable; it does not put one on a dashboard."""
    declarations = (_property(), _property(node_id="relay", property_id="closed", datatype="boolean", unit=None))
    snapshot = _snapshot(_device(properties=declarations))
    coordinator = MagicMock(data=snapshot)
    registry = dr.async_get(hass)

    entities = [
        *create_adopted_sensors(coordinator, snapshot, registry, panel_device_id="panel-device-id"),
        *create_adopted_binary_sensors(coordinator, snapshot, registry, panel_device_id="panel-device-id"),
    ]

    assert len(entities) == 2
    assert all(entity.entity_registry_enabled_default is False for entity in entities)
    assert all(entity.entity_category is EntityCategory.DIAGNOSTIC for entity in entities)


def test_a_declared_boolean_becomes_a_binary_sensor_and_not_a_sensor(hass: HomeAssistant) -> None:
    """The two creators partition the properties rather than both claiming one."""
    declarations = (_property(node_id="relay", property_id="closed", datatype="boolean", unit=None, value="true"),)
    snapshot = _snapshot(_device(properties=declarations))
    coordinator = MagicMock(data=snapshot)
    registry = dr.async_get(hass)

    assert create_adopted_sensors(coordinator, snapshot, registry, panel_device_id="p") == []
    (entity,) = create_adopted_binary_sensors(coordinator, snapshot, registry, panel_device_id="p")
    assert entity.is_on is True


# -- Identity freezes at first sighting --------------------------------------


def test_a_serial_arriving_after_adoption_does_not_move_the_device(hass: HomeAssistant) -> None:
    """The failure this rule exists to prevent, in its most likely form.

    A device adopted under its wire id that later publishes `info/serial-number`
    would, without freezing, be re-derived onto the serial -- which the registry
    reads as a device replacement and which takes the entities and their history
    with it.
    """
    registry = dr.async_get(hass)
    first_seen = adopted_identifier(PANEL_SERIAL, "generator-1")
    entry = MagicMock()
    registry.async_get_or_create = MagicMock()
    registry.async_get_device = lambda identifiers: entry if (DOMAIN, first_seen) in identifiers else None

    later = _device("generator-1", serial_number="EX-0000-0001")
    assert resolve_identifier(registry, PANEL_SERIAL, later) == first_seen


def test_a_wire_id_that_moves_keeps_a_device_adopted_under_its_serial(hass: HomeAssistant) -> None:
    """The other direction, which this repository has already been bitten by.

    Producers derive a DER's id preferring a serial over a default slug, so the
    wire id itself moves when a serial appears -- which is why PV's
    `info/serial-number` is held unvalued. A device adopted under its serial has
    to survive that.
    """
    registry = dr.async_get(hass)
    first_seen = adopted_identifier(PANEL_SERIAL, "EX-0000-0001")
    entry = MagicMock()
    registry.async_get_device = lambda identifiers: entry if (DOMAIN, first_seen) in identifiers else None

    moved = _device("panel-EX-0000-0001", serial_number="EX-0000-0001")
    assert resolve_identifier(registry, PANEL_SERIAL, moved) == first_seen


def test_a_device_never_seen_before_is_adopted_under_its_serial_when_it_has_one(hass: HomeAssistant) -> None:
    """The specification's own correlator, used where nothing is frozen yet.

    Device ids are opaque and a proxied id is `{proxier-id}-{proxied-id}`, so the
    same hardware carries different ids under different enclosures by design. The
    serial is what the specification says to correlate on.
    """
    registry = dr.async_get(hass)
    registry.async_get_device = lambda identifiers: None

    fresh = _device("generator-1", serial_number="EX-0000-0001")
    assert resolve_identifier(registry, PANEL_SERIAL, fresh) == adopted_identifier(PANEL_SERIAL, "EX-0000-0001")


def test_a_device_with_no_serial_is_adopted_under_its_wire_id(hass: HomeAssistant) -> None:
    registry = dr.async_get(hass)
    registry.async_get_device = lambda identifiers: None

    assert resolve_identifier(registry, PANEL_SERIAL, _device("generator-1")) == adopted_identifier(
        PANEL_SERIAL, "generator-1"
    )


# -- The two identifier namespaces stay readable apart -----------------------


@pytest.mark.parametrize("anchor", ["generator-1", "some-pv", "an_evse_thing", "ends-in-mid", "bess"])
def test_an_adopted_identifier_never_classifies_as_a_curated_sub_device(anchor: str) -> None:
    """The anchor is vendor vocabulary and can spell anything.

    `classify_sub_device_identifier` reads the curated grammar with suffix rules,
    so a vendor device id ending in `pv` would classify as the panel's solar
    sub-device -- which is how a device nobody modelled would end up rendered as
    one that was.
    """
    identifier = adopted_identifier(PANEL_SERIAL, anchor)
    assert ADOPTED_IDENTIFIER_TOKEN in identifier
    assert classify_sub_device_identifier(identifier) is None


def test_a_curated_identifier_still_classifies(hass: HomeAssistant) -> None:
    """The other direction: adding the adoption test must not blind the reader."""
    assert classify_sub_device_identifier(f"{PANEL_SERIAL}_bess") == "bess"
    assert classify_sub_device_identifier(f"{PANEL_SERIAL}_pv") == "pv"


# -- The pending write path is counted rather than hidden --------------------


def test_controls_are_counted_for_diagnostics() -> None:
    """A control on a device nobody modelled is the highest-consequence thing here."""
    declarations = (
        _property(datatype="boolean", settable=True, unit=None),
        _property(node_id="generator", property_id="mode", datatype="enum", fmt="AUTO,OFF", settable=True, unit=None),
        _property(),
    )
    snapshot = _snapshot(_device(properties=declarations))

    assert adopted_control_count(snapshot) == 2
    assert classify(declarations[0]) in CONTROL_PLATFORMS


def test_a_panel_with_no_adopted_device_counts_no_controls() -> None:
    assert adopted_control_count(_snapshot()) == 0


# -- Controls are built, and their domain comes from the declaration ---------


def _built(hass: HomeAssistant, *declarations: AdoptedProperty) -> dict[Platform, list[object]]:
    """Every platform's share of one adopted device, keyed by platform."""
    snapshot = _snapshot(_device(properties=declarations))
    coordinator = MagicMock(data=snapshot)
    registry = dr.async_get(hass)
    kwargs = {"panel_device_id": "panel-device-id"}
    return {
        Platform.SENSOR: list(create_adopted_sensors(coordinator, snapshot, registry, **kwargs)),
        Platform.BINARY_SENSOR: list(create_adopted_binary_sensors(coordinator, snapshot, registry, **kwargs)),
        Platform.SWITCH: list(create_adopted_switches(coordinator, snapshot, registry, **kwargs)),
        Platform.SELECT: list(create_adopted_selects(coordinator, snapshot, registry, **kwargs)),
        Platform.NUMBER: list(create_adopted_numbers(coordinator, snapshot, registry, **kwargs)),
    }


def test_every_property_reaches_exactly_one_platform(hass: HomeAssistant) -> None:
    """The partition `classify` defines, asserted as a partition.

    Five creators sharing one predicate is what makes this hold. Five bodies each
    restating it would let a property reach two platforms or none, and neither
    failure shows up in a test that only counts one platform at a time.
    """
    declarations = (
        _property(),
        _property(node_id="relay", property_id="closed", datatype="boolean", unit=None),
        _property(node_id="relay", property_id="enabled", datatype="boolean", unit=None, settable=True),
        _property(node_id="generator", property_id="mode", datatype="enum", fmt="AUTO,OFF", settable=True, unit=None),
        _property(node_id="generator", property_id="setpoint", datatype="integer", fmt="0:100:5", settable=True),
    )
    built = _built(hass, *declarations)

    assert [len(entities) for entities in built.values()] == [1, 1, 1, 1, 1]
    assert sum(len(entities) for entities in built.values()) == len(declarations)


def test_a_select_takes_its_options_from_the_declaration(hass: HomeAssistant) -> None:
    declaration = _property(
        node_id="generator", property_id="mode", datatype="enum", fmt="AUTO, MANUAL ,OFF", settable=True, unit=None
    )
    (entity,) = _built(hass, declaration)[Platform.SELECT]
    assert entity.options == ["AUTO", "MANUAL", "OFF"]


def test_a_select_reports_no_option_when_the_panel_publishes_one_it_never_declared(hass: HomeAssistant) -> None:
    """Widening the list to admit whatever arrived would hide the disagreement.

    Home Assistant rejects a `current_option` outside `options`, so the choice is
    between reporting unknown and quietly rewriting the panel's own declaration.
    """
    declaration = _property(
        node_id="generator", property_id="mode", datatype="enum", fmt="AUTO,OFF", settable=True, unit=None, value="ECO"
    )
    (entity,) = _built(hass, declaration)[Platform.SELECT]
    assert entity.current_option is None
    assert entity.options == ["AUTO", "OFF"]


def test_a_number_takes_its_bounds_from_the_declaration(hass: HomeAssistant) -> None:
    """The bounds are what made this a number rather than a reading."""
    declaration = _property(
        node_id="generator", property_id="setpoint", datatype="integer", fmt="10:80:5", settable=True, unit="%"
    )
    (entity,) = _built(hass, declaration)[Platform.NUMBER]
    assert (entity.native_min_value, entity.native_max_value, entity.native_step) == (10.0, 80.0, 5.0)


async def test_a_switch_publishes_the_vocabulary_homie_defines(hass: HomeAssistant) -> None:
    """`true` and `false`, not Home Assistant's `on` and `off`."""
    declaration = _property(node_id="relay", property_id="enabled", datatype="boolean", unit=None, settable=True)
    snapshot = _snapshot(_device(properties=(declaration,)))
    coordinator = MagicMock(data=snapshot)
    coordinator.client.set_adopted_property = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()

    (entity,) = create_adopted_switches(
        coordinator, snapshot, dr.async_get(hass), panel_device_id="panel-device-id"
    )
    await entity.async_turn_on()

    coordinator.client.set_adopted_property.assert_awaited_once_with("generator-1", "relay", "enabled", "true")


def _adopted_switch(hass: HomeAssistant) -> tuple[MagicMock, object]:
    """One adopted switch and the coordinator whose client it writes through."""
    declaration = _property(node_id="relay", property_id="enabled", datatype="boolean", unit=None, settable=True)
    snapshot = _snapshot(_device(properties=(declaration,)))
    coordinator = MagicMock(data=snapshot)
    coordinator.async_request_refresh = AsyncMock()
    (entity,) = create_adopted_switches(
        coordinator, snapshot, dr.async_get(hass), panel_device_id="panel-device-id"
    )
    return coordinator, entity


async def test_an_adopted_control_raises_when_the_command_was_not_delivered(hass: HomeAssistant) -> None:
    """A `FAILED` outcome is the promise that nothing will happen later.

    The library refuses such a write rather than letting paho queue it, so
    returning normally would tell the person who flipped the switch that their
    generator changed state when the command never left the process.
    """
    coordinator, entity = _adopted_switch(hass)
    coordinator.client.set_adopted_property = AsyncMock(
        return_value=PublishOutcome(
            state=PublishState.FAILED,
            topic=None,
            value="true",
            detail="broker not connected; refused rather than queued",
        )
    )

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_turn_on()

    assert raised.value.translation_key == "adopted_control_not_delivered"
    assert raised.value.translation_placeholders is not None
    assert raised.value.translation_placeholders["reason"] == "broker not connected; refused rather than queued"
    coordinator.async_request_refresh.assert_not_awaited()


async def test_an_adopted_control_translates_the_librarys_refusal(hass: HomeAssistant) -> None:
    """A refusal reaches the caller in the user's language, like a curated one."""
    coordinator, entity = _adopted_switch(hass)
    coordinator.client.set_adopted_property = AsyncMock(
        side_effect=SpanPanelServerError("No settable adopted property relay/enabled on device 'generator-1'")
    )

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_turn_on()

    assert raised.value.translation_key == "adopted_control_failed"
    assert raised.value.translation_placeholders is not None
    assert raised.value.translation_placeholders["reason"] == (
        "No settable adopted property relay/enabled on device 'generator-1'"
    )
    coordinator.async_request_refresh.assert_not_awaited()


async def test_a_number_publishes_an_integer_where_the_declaration_says_integer(hass: HomeAssistant) -> None:
    """`5`, never `5.0`: a float literal is outside the declared datatype."""
    declaration = _property(
        node_id="generator", property_id="setpoint", datatype="integer", fmt="0:100:5", settable=True, unit=None
    )
    snapshot = _snapshot(_device(properties=(declaration,)))
    coordinator = MagicMock(data=snapshot)
    coordinator.client.set_adopted_property = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()

    (entity,) = create_adopted_numbers(coordinator, snapshot, dr.async_get(hass), panel_device_id="panel-device-id")
    await entity.async_set_native_value(45.0)

    coordinator.client.set_adopted_property.assert_awaited_once_with("generator-1", "generator", "setpoint", "45")


def test_a_control_is_disabled_and_diagnostic_like_every_other_adopted_entity(hass: HomeAssistant) -> None:
    """Disabled-by-default is the gate, and it is the same gate for a reading.

    A second, weaker gate for controls -- surfacing them read-only -- would be
    inconsistent and would not add safety: enabling is a deliberate act,
    commanding is a second one, and the panel authorises the write regardless.
    """
    declarations = (
        _property(node_id="relay", property_id="enabled", datatype="boolean", unit=None, settable=True),
        _property(node_id="generator", property_id="mode", datatype="enum", fmt="AUTO,OFF", settable=True, unit=None),
        _property(node_id="generator", property_id="setpoint", datatype="integer", fmt="0:9:1", settable=True),
    )
    built = _built(hass, *declarations)
    controls = built[Platform.SWITCH] + built[Platform.SELECT] + built[Platform.NUMBER]

    assert len(controls) == 3
    assert all(entity.entity_registry_enabled_default is False for entity in controls)
    assert all(entity.entity_category is EntityCategory.DIAGNOSTIC for entity in controls)


# -- An adopted device exists even when it has no readings -------------------


def test_a_device_whose_whole_declaration_is_info_still_gets_a_card(hass: HomeAssistant, registered_panel: tuple[str, str]) -> None:
    """The gap explicit registration closes.

    `info` resolves entirely to the device card by the node rule, so a vendor
    device that advertises what it is before publishing any reading creates no
    entity -- and devices are otherwise created as a side effect of entity
    creation, so it used to produce *nothing at all*: no device, no entity, no
    notification. Which is the silence adoption exists to end, reached by a
    different route.
    """
    entry_id, panel_device_id = registered_panel
    device = AdoptedDevice(
        device_id="generator-1",
        device_type="energy.ebus.device.generator",
        name="Backup Generator",
        model="GEN-9000",
        vendor_name="Example Power",
        software_version="3.2.1",
        properties=(),
    )
    snapshot = _snapshot(device)

    async_register_adopted_devices(hass, entry_id, snapshot, panel_device_id=panel_device_id)

    registered = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, adopted_identifier(PANEL_SERIAL, "generator-1"))}
    )
    assert registered is not None
    assert registered.name == "Backup Generator"
    assert registered.model == "GEN-9000"
    assert registered.manufacturer == "Example Power"
    assert registered.sw_version == "3.2.1"


def test_registration_freezes_the_anchor_before_any_entity_resolves_it(hass: HomeAssistant, registered_panel: tuple[str, str]) -> None:
    """Registering first is what makes the freeze single-valued.

    `resolve_identifier` reads the registry to decide which spelling this install
    uses. Running it once at registration means every entity created afterwards
    resolves against a device that already exists and cannot disagree -- including
    on the run where a serial first arrives.
    """
    entry_id, panel_device_id = registered_panel
    without_serial = _device("generator-1")
    async_register_adopted_devices(hass, entry_id, _snapshot(without_serial), panel_device_id=panel_device_id)

    with_serial = _device("generator-1", serial_number="EX-0000-0001")
    async_register_adopted_devices(hass, entry_id, _snapshot(with_serial), panel_device_id=panel_device_id)

    registry = dr.async_get(hass)
    assert registry.async_get_device(identifiers={(DOMAIN, adopted_identifier(PANEL_SERIAL, "generator-1"))})
    assert registry.async_get_device(identifiers={(DOMAIN, adopted_identifier(PANEL_SERIAL, "EX-0000-0001"))}) is None


def test_a_panel_with_no_adopted_device_registers_nothing(hass: HomeAssistant, registered_panel: tuple[str, str]) -> None:
    entry_id, panel_device_id = registered_panel
    async_register_adopted_devices(hass, entry_id, _snapshot(), panel_device_id=panel_device_id)

    adopted = [
        device
        for device in dr.async_get(hass).devices.values()
        if any(ADOPTED_IDENTIFIER_TOKEN in identifier for _domain, identifier in device.identifiers)
    ]
    assert adopted == []


# -- The id grammar is one grammar -------------------------------------------


def test_an_adopted_id_follows_the_same_grammar_as_a_curated_one() -> None:
    """`span_{serial}_{scope}_{suffix}`, with the serial spelled the same way.

    An earlier version lower-cased and de-hyphenated the whole string, which
    mangled the serial into `span_sp3_242424_001_...` where every other id in the
    integration says `span_sp3-242424-001_...`. A reader that parses an id by
    position -- `extract_circuit_uuid_from_unique_id` does -- must not meet a
    second grammar.
    """
    declaration = _property(node_id="meter", property_id="active-power")
    identifier = adopted_identifier(PANEL_SERIAL, "generator-1")

    adopted = adopted_unique_id(identifier, declaration)
    curated = build_panel_unique_id(PANEL_SERIAL, "panel.instant_grid_power_w")

    prefix = f"span_{PANEL_SERIAL}_"
    assert adopted.startswith(prefix)
    assert curated.startswith(prefix)
    assert adopted == f"{prefix}{ADOPTED_IDENTIFIER_TOKEN}_generator-1_meter_active_power"


def test_the_wire_address_is_snake_cased_and_the_anchor_is_not() -> None:
    """The suffix has to read like a curated suffix; the anchor is an identity.

    A serial keeps its hyphens in every curated id, and an adopted anchor is the
    same kind of thing -- a name the device is known by, not a description key.
    """
    declaration = _property(node_id="charge-limit", property_id="owner-limit")
    identifier = adopted_identifier(PANEL_SERIAL, "EX-0000-0001")

    assert adopted_unique_id(identifier, declaration).endswith("_EX-0000-0001_charge_limit_owner_limit".lower())


def test_curation_changes_two_of_the_three_segments(hass: HomeAssistant) -> None:
    """Why uniform grammar does not remove the migration promotion needs.

    The serial is the same either way. The scope becomes a curated sub-device kind
    rather than `adopted_{anchor}`, and the suffix becomes a human-chosen
    description key rather than a wire address. Both are the change itself, not a
    formatting difference -- so a curated description cannot reproduce the adopted
    id and has to take it over instead.
    """
    declaration = _property(node_id="meter", property_id="active-power")
    adopted = adopted_unique_id(adopted_identifier(PANEL_SERIAL, "generator-1"), declaration)
    curated_if_modelled = build_panel_unique_id(PANEL_SERIAL, "generator.active_power")

    assert adopted != curated_if_modelled
    assert ADOPTED_IDENTIFIER_TOKEN in adopted
    assert ADOPTED_IDENTIFIER_TOKEN not in curated_if_modelled


# -- Diagnostics report the proxy relationship, never the parent's id ---------


def test_diagnostics_report_whether_a_device_is_proxied_and_not_by_whom() -> None:
    """A device id can embed a serial, so the id must not go in the payload.

    Producers derive a DER's id preferring a serial over a default slug -- which
    is why this repository holds PV's `info/serial-number` unvalued -- so
    reporting `parent` verbatim would leak the serial the block deliberately
    withholds. The boolean answers the maintainer's actual question.
    """
    device = replace(_device("gateway-1-sensor"), parent="gateway-1", proxied=True)
    block = _adoption(_snapshot(device))

    (row,) = block["devices"]
    assert row["proxied"] is True
    assert "parent" not in row
    assert "gateway-1" not in json.dumps(block)
