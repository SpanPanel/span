"""Vendor extensions on curated devices become entities, on the right card, forever.

Three properties carry this half of the design and each fails loudly here if it
stops holding: an extension entity lands on the device it belongs to and never
mints a card of its own, the platform a row is born under is the platform it
keeps however the declaration changes, and nothing is created for a card that is
not there yet.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.persistent_notification import async_dismiss
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE, EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import ExtensionProperty, ExtensionSubject

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.curation import CurationOverlay, CurationRecord
from custom_components.span_panel.extension import (
    HINT_DETAIL,
    HINT_READING,
    MAX_PER_DEVICE,
    ExtensionBinarySensor,
    ExtensionSensor,
    adoptable,
    async_notice_declined_extensions,
    classify_extension,
    create_extension_binary_sensors,
    create_extension_sensors,
    extension_curation_key,
    extension_device_identifier,
    extension_scope,
    extension_unique_id,
    prominence_hint,
    resolve_platform,
    subject_key,
)
from custom_components.span_panel.notices import _DATA, async_restore
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
    assert adoptable(snapshot, dr.async_get(hass), er.async_get(hass)) == []


def test_a_row_on_a_registered_card_is_adoptable(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    snapshot = _snapshot(_row())
    adoptable_rows = adoptable(snapshot, dr.async_get(hass), er.async_get(hass))
    assert len(adoptable_rows) == 1
    row, unique_id, identifier = adoptable_rows[0]
    assert identifier == BESS_IDENTIFIER
    assert unique_id == "span_sp3-242424-001_adopted_bess/battery-2/cell-temperature"
    assert row.path == "battery-2/cell-temperature"


def test_an_off_charset_address_is_declined_rather_than_sanitised(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    snapshot = _snapshot(_row(property_id="Cell_Temperature"))
    assert adoptable(snapshot, dr.async_get(hass), er.async_get(hass)) == []


# --- the cap ----------------------------------------------------------------


def test_a_vendor_flooding_one_device_is_capped(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """Registry rows are permanent and nothing removes them, so the flood is bounded."""
    rows = tuple(_row(property_id=f"reading-{index}") for index in range(MAX_PER_DEVICE + 25))
    adopted = adoptable(_snapshot(*rows), dr.async_get(hass), er.async_get(hass))
    assert len(adopted) == MAX_PER_DEVICE


def test_the_cap_is_per_wire_device_not_per_card(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """One noisy device must not crowd out a quiet one that shares its card.

    The panel, every circuit and both lugs render on the panel's card. Counting
    per card would pool thirty-five wire devices against one allowance, so two
    vendor properties on each circuit of a 32-circuit panel would truncate with
    no misbehaving publisher anywhere. The cap counts the wire device.
    """
    noisy = tuple(
        _row(kind="circuit", instance_key="circuit-a", node_id="acme", property_id=f"reading-{index}")
        for index in range(MAX_PER_DEVICE + 5)
    )
    quiet = (
        _row(kind="circuit", instance_key="circuit-b", node_id="acme", property_id="reading-0"),
        _row(kind="panel", node_id="acme", property_id="site-reading"),
        _row(kind="lugs", instance_key="upstream", node_id="acme", property_id="phase-balance"),
    )
    adopted = adoptable(_snapshot(*noisy, *quiet), dr.async_get(hass), er.async_get(hass))

    # Every quiet device keeps its readings, though all four share the panel card.
    adopted_keys = [subject_key(row.subject) for row, _uid, _identifier in adopted]
    assert adopted_keys.count("circuit:circuit-a") == MAX_PER_DEVICE
    assert adopted_keys.count("circuit:circuit-b") == 1
    assert adopted_keys.count("panel") == 1
    assert adopted_keys.count("lugs:upstream") == 1
    assert {identifier for _row_, _uid, identifier in adopted} == {PANEL_SERIAL}


def test_two_lugs_publishing_the_same_property_get_two_identities(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """The collision that folding lugs into `panel` produced.

    Both lugs devices run the same firmware, so a vendor extension on one is the
    expected case of the same extension on both. One subject for the pair minted
    one unique_id for two readings: Home Assistant drops the second, and the
    survivor shows whichever device sorted first.
    """
    upstream = _row(kind="lugs", instance_key="upstream", node_id="acme", property_id="phase-balance", value="1.5")
    downstream = _row(kind="lugs", instance_key="downstream", node_id="acme", property_id="phase-balance", value="99.9")
    adopted = adoptable(_snapshot(upstream, downstream), dr.async_get(hass), er.async_get(hass))

    ids = [unique_id for _row_, unique_id, _identifier in adopted]
    assert len(ids) == len(set(ids)) == 2
    # Both still render on the panel's card: identity distinguishes, placement merges.
    assert {identifier for _row_, _uid, identifier in adopted} == {PANEL_SERIAL}


def test_a_registered_entity_is_never_displaced_by_the_cap(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """A standing entity outranks a new arrival, whatever order the wire sends them in.

    The row order tracks the wire, so a firmware update declaring a property
    earlier shifts everything after it. Capping on arrival order alone would let
    a new property evict a standing entity whose registry row is permanent and
    for which nothing would ever build an entity again -- unavailable forever,
    with no migration path by design.
    """
    registry = er.async_get(hass)
    standing = _row(property_id="long-standing")
    standing_id = extension_unique_id(PANEL_SERIAL, standing.subject, standing.node_id, standing.property_id)
    assert standing_id is not None
    registry.async_get_or_create(Platform.SENSOR.value, DOMAIN, standing_id)

    # The standing property now arrives *last*, behind a full cap of new ones.
    newcomers = tuple(_row(property_id=f"new-{index}") for index in range(MAX_PER_DEVICE))
    adopted = adoptable(_snapshot(*newcomers, standing), dr.async_get(hass), registry)

    assert standing_id in {unique_id for _row_, unique_id, _identifier in adopted}
    assert len(adopted) == MAX_PER_DEVICE


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
        _coordinator(snapshot),
        snapshot,
        dr.async_get(hass),
        er.async_get(hass),
        overlay=CurationOverlay.empty(),
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
        _coordinator(snapshot),
        snapshot,
        dr.async_get(hass),
        er.async_get(hass),
        overlay=CurationOverlay.empty(),
    )[0]
    assert sensor._attr_name == "Battery 2 Cell Temperature"


def test_a_declared_boolean_becomes_a_binary_sensor(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    snapshot = _snapshot(
        _row(property_id="pack-enabled", datatype="boolean", unit=None, value="true")
    )
    binary = create_extension_binary_sensors(
        _coordinator(snapshot),
        snapshot,
        dr.async_get(hass),
        er.async_get(hass),
        overlay=CurationOverlay.empty(),
    )
    assert len(binary) == 1
    assert isinstance(binary[0], ExtensionBinarySensor)
    assert binary[0].is_on is True
    # And it is not also a sensor: one property, one platform.
    assert (
        create_extension_sensors(
            _coordinator(snapshot),
            snapshot,
            dr.async_get(hass),
            er.async_get(hass),
            overlay=CurationOverlay.empty(),
        )
        == []
    )


def test_a_property_that_stops_being_published_reads_unknown_rather_than_vanishing(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """Absence on the wire is ambiguous, so the entity stays and reports nothing."""
    snapshot = _snapshot(_row())
    sensor = create_extension_sensors(
        _coordinator(snapshot),
        snapshot,
        dr.async_get(hass),
        er.async_get(hass),
        overlay=CurationOverlay.empty(),
    )[0]

    sensor.coordinator.data = _snapshot()
    assert sensor.native_value is None


def test_an_unparseable_number_is_reported_as_nothing_rather_than_as_text(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """A string behind a unit and a device class is a worse lie than no reading."""
    snapshot = _snapshot(_row(value="not-a-number"))
    sensor = create_extension_sensors(
        _coordinator(snapshot),
        snapshot,
        dr.async_get(hass),
        er.async_get(hass),
        overlay=CurationOverlay.empty(),
    )[0]
    assert sensor.native_value is None


# --- what the owner of the device is allowed to assert ----------------------


def _overlay_keyed(row: ExtensionProperty, record: CurationRecord) -> CurationOverlay:
    """Key a record the way `_create` looks one up: the scope segment, then the wire path."""
    return CurationOverlay({f"{extension_scope(row.subject)}/{row.path}": record})


def test_the_curation_key_is_scoped_because_a_wire_path_alone_is_not_unique() -> None:
    """Three wire devices publishing one path are three rows, so they are three keys.

    `path` is unique only within one wire device, so keying on it bare would let
    a record asserted for one circuit reshape the identically-named property on
    every other circuit -- and on the battery. The scope segment is exactly the
    one `extension_unique_id` carries, so the key is injective for the reason
    the id is.
    """
    rows = (
        _row(),
        _row(kind="circuit", instance_key="circuit-a"),
        _row(kind="circuit", instance_key="circuit-b"),
    )
    assert {extension_curation_key(row.subject, row.path) for row in rows} == {
        "bess/battery-2/cell-temperature",
        "circuit_circuit-a/battery-2/cell-temperature",
        "circuit_circuit-b/battery-2/cell-temperature",
    }


def test_a_subject_that_names_no_card_has_nothing_to_curate() -> None:
    """`None` mirrors `extension_unique_id`: no card, no entity, and so no key."""
    subject = ExtensionSubject(kind="thermostat", instance_key=None)
    assert extension_curation_key(subject, "acme/setpoint") is None


def test_a_curated_record_shapes_the_extension_sensor(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """Every field the owner of the device may assert reaches the entity.

    Including the `state_class` this module may not infer: the entity is built
    from a description `curation` composed, so a user's assertion arrives
    without `extension.py` ever naming the thing its AST guard forbids.
    """
    row = _row(datatype="float", unit="V")
    snapshot = _snapshot(row)
    record = CurationRecord(
        state_class=SensorStateClass.MEASUREMENT, device_class="voltage", promote=True
    )
    (entity,) = create_extension_sensors(
        _coordinator(snapshot),
        snapshot,
        dr.async_get(hass),
        er.async_get(hass),
        overlay=_overlay_keyed(row, record),
    )
    assert entity.state_class is SensorStateClass.MEASUREMENT
    assert entity.device_class is SensorDeviceClass.VOLTAGE
    assert entity.entity_category is None
    # Promotion is not enablement. Enabling stays the user's separate act.
    assert entity.entity_registry_enabled_default is False


def test_an_uncurated_extension_row_is_exactly_todays_entity(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """Curation adds a path; it moves nothing that nobody curated."""
    row = _row(datatype="float", unit="V")
    snapshot = _snapshot(row)
    (entity,) = create_extension_sensors(
        _coordinator(snapshot),
        snapshot,
        dr.async_get(hass),
        er.async_get(hass),
        overlay=CurationOverlay.empty(),
    )
    assert entity.state_class is None
    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    # The unit map still supplies what the wire declared.
    assert entity.device_class is SensorDeviceClass.VOLTAGE


def test_a_stale_extension_record_field_is_skipped_and_the_rest_applied(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """A record outlives the declaration it was asserted against, and must not fail setup.

    The publisher has relabelled a numeric property as a string, so the stored
    state class no longer fits. It is dropped -- the row is read through
    `for_row` rather than off the overlay -- and the prominence the same user
    asserted is honoured all the same.
    """
    row = _row(datatype="string", unit=None)
    snapshot = _snapshot(row)
    record = CurationRecord(state_class=SensorStateClass.MEASUREMENT, promote=True)
    (entity,) = create_extension_sensors(
        _coordinator(snapshot),
        snapshot,
        dr.async_get(hass),
        er.async_get(hass),
        overlay=_overlay_keyed(row, record),
    )
    assert entity.state_class is None
    assert entity.entity_category is None


def test_a_curated_binary_sensor_gets_the_only_device_class_there_can_be(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """A boolean declares no unit, so `door` and `problem` are indistinguishable on the wire.

    There is nothing to default from, which makes the user's assertion the only
    device class a vendor boolean can ever carry.
    """
    row = _row(property_id="pack-fault", datatype="boolean", unit=None, value="true")
    snapshot = _snapshot(row)
    (entity,) = create_extension_binary_sensors(
        _coordinator(snapshot),
        snapshot,
        dr.async_get(hass),
        er.async_get(hass),
        overlay=_overlay_keyed(row, CurationRecord(device_class="problem")),
    )
    assert entity.device_class == BinarySensorDeviceClass.PROBLEM
    assert entity.entity_category is EntityCategory.DIAGNOSTIC


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
        _coordinator(snapshot),
        snapshot,
        dr.async_get(hass),
        er.async_get(hass),
        overlay=CurationOverlay.empty(),
    )[0]
    assert sensor._attr_extra_state_attributes["prominence_hint"] == HINT_READING
    assert sensor._attr_extra_state_attributes["wire_path"] == "battery-2/cell-temperature"


# --- the overflow notice is told once, not at every setup --------------------


def _overflowing(count: int = MAX_PER_DEVICE + 3) -> SpanPanelSnapshot:
    """One battery declaring more vendor properties than the cap will admit."""
    return _snapshot(*(_row(property_id=f"cell-{index}") for index in range(count)))


def _notification_id(entry: MockConfigEntry) -> str:
    return f"{DOMAIN}_extension_overflow_{entry.entry_id}"


async def _notice(hass: HomeAssistant, entry: MockConfigEntry, snapshot: SpanPanelSnapshot) -> None:
    await async_notice_declined_extensions(
        hass, entry, snapshot, dr.async_get(hass), er.async_get(hass)
    )


async def test_the_overflow_notice_is_raised_when_the_cap_declines_something(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """A silent truncation reads as "that is everything the vendor publishes"."""
    entry = hass.config_entries.async_get_entry(registered_panel[0])
    assert entry is not None
    await async_restore(hass, entry)

    await _notice(hass, entry, _overflowing())

    assert _notification_id(entry) in dict(hass.data.get("persistent_notification", {}))


async def test_a_dismissed_overflow_notice_is_not_raised_again_next_setup(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """`notices.py:21-26` states the rule this violated: dismissal is the acknowledgement.

    The cap is re-derived from the same wire on every setup, so re-raising made
    the notice un-dismissable -- it came back on every restart and every reload
    for as long as the publisher kept publishing, which is forever.
    """
    entry = hass.config_entries.async_get_entry(registered_panel[0])
    assert entry is not None
    await async_restore(hass, entry)
    snapshot = _overflowing()
    await _notice(hass, entry, snapshot)

    async_dismiss(hass, _notification_id(entry))
    await hass.async_block_till_done()
    await _notice(hass, entry, snapshot)

    assert _notification_id(entry) not in dict(hass.data.get("persistent_notification", {}))


async def test_a_dismissed_overflow_notice_returns_when_more_is_declined(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """Remembering the announcement may not silence a *different* announcement.

    A second vendor device overflowing later is news the first notice never
    carried, so the record is of what was said rather than of having said
    something.
    """
    entry = hass.config_entries.async_get_entry(registered_panel[0])
    assert entry is not None
    await async_restore(hass, entry)
    await _notice(hass, entry, _overflowing())
    async_dismiss(hass, _notification_id(entry))
    await hass.async_block_till_done()

    await _notice(hass, entry, _overflowing(MAX_PER_DEVICE + 9))

    assert _notification_id(entry) in dict(hass.data.get("persistent_notification", {}))


async def test_the_overflow_record_survives_a_restart(
    hass: HomeAssistant, registered_panel: tuple[str, str]
) -> None:
    """The in-memory view dies with the process; the reason it must not come back does not."""
    entry = hass.config_entries.async_get_entry(registered_panel[0])
    assert entry is not None
    await async_restore(hass, entry)
    snapshot = _overflowing()
    await _notice(hass, entry, snapshot)
    async_dismiss(hass, _notification_id(entry))
    await hass.async_block_till_done()

    # Flushed through core's own final-write event rather than by advancing the
    # clock. A dismissal and a raise each queue a delayed save, and a `Store`
    # that has been re-armed will reschedule its timer rather than write when a
    # test fires it early -- so the clock trick reports "nothing was recorded"
    # for a record that was. This is the path Home Assistant takes at shutdown.
    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done()
    hass.data.get(_DATA, {}).pop(entry.entry_id, None)
    hass.data.get("persistent_notification", {}).clear()
    await async_restore(hass, entry)
    await _notice(hass, entry, snapshot)

    assert _notification_id(entry) not in dict(hass.data.get("persistent_notification", {}))


def test_no_state_class_is_set_anywhere_in_the_extension_module() -> None:
    """Assert the no-statistics rule against the syntax, not against an instance.

    This closes the gap the adoption module's guard left: `extension.py` had only
    per-instance coverage, so a future branch setting a state class on a platform
    no test instantiates would pass everything above. The one module allowed to
    spell `state_class` is `curation.py`, where every value comes from a
    validated user record.
    """
    from custom_components.span_panel import extension

    tree = ast.parse(Path(extension.__file__).read_text(encoding="utf-8"))
    keywords = [node.arg for node in ast.walk(tree) if isinstance(node, ast.keyword)]
    targets = [node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)]
    assert "state_class" not in keywords
    assert "_attr_state_class" not in targets
    assert not [
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and "StateClass" in node.id
    ]
