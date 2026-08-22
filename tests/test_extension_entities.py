"""Vendor extensions on curated devices become entities, on the right card, forever.

Three properties carry this half of the design and each fails loudly here if it
stops holding: an extension entity lands on the device it belongs to and never
mints a card of its own, the platform a row is born under is the platform it
keeps however the declaration changes, and nothing is created for a card that is
not there yet.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import ExtensionProperty, ExtensionSubject

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.extension import (
    HINT_DETAIL,
    HINT_READING,
    MAX_PER_DEVICE,
    ExtensionBinarySensor,
    ExtensionSensor,
    adoptable,
    classify_extension,
    create_extension_binary_sensors,
    create_extension_sensors,
    extension_device_identifier,
    prominence_hint,
    resolve_platform,
)
from custom_components.span_panel.util import SUB_DEVICE_BESS

from .factories import SpanPanelSnapshotFactory

if TYPE_CHECKING:
    from span_panel_api import SpanPanelSnapshot

PANEL_SERIAL = "sp3-242424-001"
BESS_IDENTIFIER = f"{PANEL_SERIAL}_{SUB_DEVICE_BESS}"


@pytest.fixture
def registered_panel(hass: HomeAssistant) -> tuple[str, str]:
    """Return a config entry with the panel and its BESS card registered, as setup leaves them."""
    mock = MockConfigEntry(domain=DOMAIN, data={}, unique_id=PANEL_SERIAL)
    mock.add_to_hass(hass)
    registry = dr.async_get(hass)
    panel = registry.async_get_or_create(
        config_entry_id=mock.entry_id,
        identifiers={(DOMAIN, PANEL_SERIAL)},
        name="Span Panel",
    )
    registry.async_get_or_create(
        config_entry_id=mock.entry_id,
        identifiers={(DOMAIN, BESS_IDENTIFIER)},
        name="Span Panel Battery",
        via_device_id=panel.id,
    )
    return str(mock.entry_id), panel.id


def _row(
    node_id: str = "battery-2",
    property_id: str = "cell-temperature",
    datatype: str = "float",
    unit: str | None = "°C",
    value: str | None = "31.4",
    kind: str = "battery",
    instance_key: str | None = None,
    settable: bool = False,
) -> ExtensionProperty:
    return ExtensionProperty(
        subject=ExtensionSubject(kind=kind, instance_key=instance_key),
        node_id=node_id,
        property_id=property_id,
        datatype=datatype,
        unit=unit,
        value=value,
        settable=settable,
    )


def _snapshot(*rows: ExtensionProperty) -> SpanPanelSnapshot:
    """Return a complete curated snapshot carrying the given extension rows."""
    return replace(
        SpanPanelSnapshotFactory.create_complete(serial_number=PANEL_SERIAL),
        extension_properties=rows,
    )


def _coordinator(snapshot: SpanPanelSnapshot) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = snapshot
    return coordinator


# --- the platform table, and the one-way door -------------------------------


@pytest.mark.parametrize(
    ("datatype", "expected"),
    [
        ("boolean", Platform.BINARY_SENSOR),
        ("float", Platform.SENSOR),
        ("integer", Platform.SENSOR),
        ("enum", Platform.SENSOR),
        ("string", Platform.SENSOR),
    ],
)
def test_two_platforms_only(datatype: str, expected: Platform) -> None:
    """No controls, whatever the declaration says -- adoption's three rows are absent."""
    assert classify_extension(datatype) is expected


def test_a_settable_property_is_still_a_reading() -> None:
    """The read-only ruling, at the classifier.

    A control here would sit beside curated controls that do real safety work on
    the same wire -- the EVSE limit refuses a value above the commissioned
    ceiling -- with none of their translation or bounds.
    """
    assert classify_extension("enum") is Platform.SENSOR
    assert classify_extension("float") is Platform.SENSOR


def test_the_platform_a_row_is_born_under_is_the_one_it_keeps(hass: HomeAssistant) -> None:
    """Metadata may reshape an entity; it may never move its domain.

    The registry refuses a cross-domain rename outright, so re-deriving the
    platform from a changed declaration would not move the row -- it would strand
    it and mint a second entity beside it.
    """
    registry = er.async_get(hass)
    unique_id = "span_sp3-242424-001_adopted_bess/battery-2/cell-temperature"
    registry.async_get_or_create(
        Platform.SENSOR.value, DOMAIN, unique_id, suggested_object_id="battery_2_cell_temperature"
    )

    # The publisher relabels the property as a boolean. The row stays a sensor.
    assert resolve_platform(registry, unique_id, "boolean") is Platform.SENSOR


def test_an_unregistered_id_takes_the_platform_its_datatype_implies(hass: HomeAssistant) -> None:
    assert (
        resolve_platform(er.async_get(hass), "span_x_adopted_bess/n/p", "boolean")
        is Platform.BINARY_SENSOR
    )


# --- placement --------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "instance_key", "expected"),
    [
        ("panel", None, PANEL_SERIAL),
        ("circuit", "abc123", PANEL_SERIAL),
        ("battery", None, f"{PANEL_SERIAL}_bess"),
        ("mid", None, f"{PANEL_SERIAL}_mid"),
        ("pv", None, f"{PANEL_SERIAL}_pv"),
        ("evse", "acme-001", f"{PANEL_SERIAL}_evse_acme-001"),
    ],
)
def test_each_subject_resolves_to_an_existing_card(
    kind: str, instance_key: str | None, expected: str
) -> None:
    """A circuit's entities live on the panel's card, as its curated ones do."""
    subject = ExtensionSubject(kind=kind, instance_key=instance_key)
    assert extension_device_identifier(PANEL_SERIAL, subject) == expected


def test_a_row_whose_card_is_not_registered_yet_is_deferred(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """A capability race defers the entity to the next reload rather than minting a card."""
    snapshot = _snapshot(_row(kind="pv"))
    assert adoptable(snapshot, dr.async_get(hass)) == []


def test_a_row_on_a_registered_card_is_adoptable(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    snapshot = _snapshot(_row())
    adoptable_rows = adoptable(snapshot, dr.async_get(hass))
    assert len(adoptable_rows) == 1
    row, unique_id, identifier = adoptable_rows[0]
    assert identifier == BESS_IDENTIFIER
    assert unique_id == "span_sp3-242424-001_adopted_bess/battery-2/cell-temperature"
    assert row.path == "battery-2/cell-temperature"


def test_an_off_charset_address_is_declined_rather_than_sanitised(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    snapshot = _snapshot(_row(property_id="Cell_Temperature"))
    assert adoptable(snapshot, dr.async_get(hass)) == []


# --- the cap ----------------------------------------------------------------


def test_a_vendor_flooding_one_device_is_capped(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """Registry rows are permanent and nothing removes them, so the flood is bounded."""
    rows = tuple(_row(property_id=f"reading-{index}") for index in range(MAX_PER_DEVICE + 25))
    adopted = adoptable(_snapshot(*rows), dr.async_get(hass))
    assert len(adopted) == MAX_PER_DEVICE


def test_the_cap_is_per_device_not_per_panel(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """One noisy vendor device must not crowd out a quiet one on another card."""
    battery_rows = tuple(
        _row(property_id=f"reading-{index}") for index in range(MAX_PER_DEVICE + 5)
    )
    panel_row = _row(kind="panel", node_id="acme", property_id="site-reading")
    adopted = adoptable(_snapshot(*battery_rows, panel_row), dr.async_get(hass))
    assert sum(1 for _row_, _uid, identifier in adopted if identifier == PANEL_SERIAL) == 1


# --- the entities themselves ------------------------------------------------


def test_a_sensor_arrives_disabled_diagnostic_and_without_statistics(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """The arrival state, and the one guarantee that makes reshaping safe.

    No `state_class` means no long-term statistics, so a later unit or
    device-class change has nothing to corrupt.
    """
    snapshot = _snapshot(_row())
    sensors = create_extension_sensors(
        _coordinator(snapshot), snapshot, dr.async_get(hass), er.async_get(hass)
    )
    assert len(sensors) == 1
    sensor = sensors[0]
    assert isinstance(sensor, ExtensionSensor)
    assert sensor._attr_entity_registry_enabled_default is False
    assert sensor._attr_entity_category is EntityCategory.DIAGNOSTIC
    assert getattr(sensor.entity_description, "state_class", None) is None
    assert sensor.native_value == 31.4
    assert sensor.entity_description.native_unit_of_measurement == "°C"


def test_a_name_carries_the_node_so_it_cannot_collide_with_a_curated_one(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """Curated names on these cards carry no wire vocabulary, so prefixing avoids collisions."""
    snapshot = _snapshot(_row())
    sensor = create_extension_sensors(
        _coordinator(snapshot), snapshot, dr.async_get(hass), er.async_get(hass)
    )[0]
    assert sensor._attr_name == "Battery 2 Cell Temperature"


def test_a_declared_boolean_becomes_a_binary_sensor(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    snapshot = _snapshot(
        _row(property_id="pack-enabled", datatype="boolean", unit=None, value="true")
    )
    binary = create_extension_binary_sensors(
        _coordinator(snapshot), snapshot, dr.async_get(hass), er.async_get(hass)
    )
    assert len(binary) == 1
    assert isinstance(binary[0], ExtensionBinarySensor)
    assert binary[0].is_on is True
    # And it is not also a sensor: one property, one platform.
    assert (
        create_extension_sensors(
            _coordinator(snapshot), snapshot, dr.async_get(hass), er.async_get(hass)
        )
        == []
    )


def test_a_property_that_stops_being_published_reads_unknown_rather_than_vanishing(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """Absence on the wire is ambiguous, so the entity stays and reports nothing."""
    snapshot = _snapshot(_row())
    sensor = create_extension_sensors(
        _coordinator(snapshot), snapshot, dr.async_get(hass), er.async_get(hass)
    )[0]

    sensor.coordinator.data = _snapshot()
    assert sensor.native_value is None


def test_an_unparseable_number_is_reported_as_nothing_rather_than_as_text(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """A string behind a unit and a device class is a worse lie than no reading."""
    snapshot = _snapshot(_row(value="not-a-number"))
    sensor = create_extension_sensors(
        _coordinator(snapshot), snapshot, dr.async_get(hass), er.async_get(hass)
    )[0]
    assert sensor.native_value is None


# --- the prominence hint ----------------------------------------------------


def test_identity_naming_outranks_a_unit() -> None:
    """The highest-confidence signal is negative, which is why it is checked first."""
    assert prominence_hint(_row(property_id="firmware-version", unit=None)) == HINT_DETAIL
    assert prominence_hint(_row(property_id="pack-serial-number", unit="W")) == HINT_DETAIL


def test_a_unit_with_a_device_class_leans_reading() -> None:
    assert prominence_hint(_row(property_id="cell-temperature", unit="°C")) == HINT_READING
    assert prominence_hint(_row(property_id="acme-power", unit="W")) == HINT_READING


def test_a_percentage_is_never_promoted() -> None:
    """`%` is a state of charge, a confidence, or a duty cycle, and nothing tells them apart.

    The systematic false negative the design accepts: the most headline-worthy
    number a battery publishes lands as a detail, and `entity_category` is free
    to revise later.
    """
    assert prominence_hint(_row(property_id="state-of-charge", unit="%")) == HINT_DETAIL


def test_the_hint_is_carried_on_the_entity_for_curation_triage(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    snapshot = _snapshot(_row())
    sensor = create_extension_sensors(
        _coordinator(snapshot), snapshot, dr.async_get(hass), er.async_get(hass)
    )[0]
    assert sensor._attr_extra_state_attributes["prominence_hint"] == HINT_READING
    assert sensor._attr_extra_state_attributes["wire_path"] == "battery-2/cell-temperature"
