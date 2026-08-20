"""Devices this integration models nothing for surface without disturbing curated ones.

Three properties carry the whole design and each has a test here that fails if it
stops holding: nothing adopted enters long-term statistics, an adopted device
keeps the identity it was first seen under, and the notice counts adopted devices
rather than listing their entities.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import pytest
from span_panel_api import AdoptedDevice, AdoptedProperty

from custom_components.span_panel.adoption import (
    CONTROL_PLATFORMS,
    DEVICE_CLASS_BY_UNIT,
    adopted_control_count,
    adopted_identifier,
    classify,
    create_adopted_binary_sensors,
    create_adopted_sensors,
    resolve_identifier,
)
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.util import (
    ADOPTED_IDENTIFIER_TOKEN,
    classify_sub_device_identifier,
)

from .factories import SpanPanelSnapshotFactory

if TYPE_CHECKING:
    from span_panel_api import SpanPanelSnapshot

PANEL_SERIAL = "sp3-242424-001"


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


def test_a_settable_property_is_counted_as_a_pending_control() -> None:
    """`classify` is the complete rule; the write path is what does not exist yet.

    Every write this integration performs goes through a curated, adapter-named
    topic, and a generic one would put a new member on `SchemaAdapter` -- required
    of every adapter package, invalidating built wheels. That is its own change.
    Counting the properties waiting on it is what decides whether it is worth one.
    """
    declarations = (
        _property(datatype="boolean", settable=True, unit=None),
        _property(node_id="generator", property_id="mode", datatype="enum", fmt="AUTO,OFF", settable=True, unit=None),
        _property(),
    )
    snapshot = _snapshot(_device(properties=declarations))

    assert adopted_control_count(snapshot) == 2
    assert classify(declarations[0]) in CONTROL_PLATFORMS


def test_a_panel_with_no_adopted_device_counts_no_pending_controls() -> None:
    assert adopted_control_count(_snapshot()) == 0
