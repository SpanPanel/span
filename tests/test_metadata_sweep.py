"""Identity that reaches a device card, an attribute or a diagnostic sensor.

Four surfaces, one new entity class, and one of them is a regression rather
than a feature. Grouped because they share a proof obligation: each is a value
the panel has published all along that nothing rendered, so a test asserting a
constant the code also holds would pass whether or not the wire is ever read.

Every expectation below is therefore read out of the reference capture, and every
reading is proved by republishing it, unpublishing it, or both. The device-card
assertions go through the real device registry after a real registration rather
than through the `DeviceInfo` dict, because the dict is what the code returns and
the registry is what a user sees.

**`panel.wifi_ssid` is the regression.** Flat published `core/wifi-ssid` and the
integration has surfaced it as an attribute since; v1.0 declares
`status/wifi-ssid`, schema_1 mapped nothing to it, and the path's exemption
annotation said `SCHEMA_0_ONLY` -- which was true, and sanctioned a user losing
an attribute on upgrade. With the library reading it, both adapters produce the
path, so it is a declaration now and the producible gate covers it.

It is published on the **Wi-Fi Link binary sensor**, which is the coherent host:
the entity that reports whether Wi-Fi is up is the one that should say which
network it is up on, and both values come off the same node on the wire. The
Software Version sensor no longer carries it. That narrowing had already begun
undocumented -- at v2.0.8 four `STATUS_SENSORS` descriptions rendered the
attribute and three have since moved elsewhere, leaving one -- and this finishes
it and writes it down.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel import SpanPanelRuntimeData, ensure_device_registered
from custom_components.span_panel.binary_sensor import (
    SpanPanelWifiLinkBinarySensor,
    async_setup_entry as binary_sensor_async_setup_entry,
)
from custom_components.span_panel.const import DOMAIN, SYSTEM_DOOR_STATE, SYSTEM_WIFI_LINK
from custom_components.span_panel.field_paths import (
    RESIDUAL_EXEMPT_PATHS,
    declared_field_paths,
)
from custom_components.span_panel.sensor import create_evse_sensors, create_panel_sensors
from custom_components.span_panel.sensor_definitions import EVSE_SENSORS
from custom_components.span_panel.sensor_panel import SpanPanelPanelStatus, SpanPanelStatus
from homeassistant.const import CONF_HOST, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .adapter_fixtures import SCHEMA_ONE_PANEL, schema_one_snapshot, schema_one_tree

from pytest_homeassistant_custom_component.common import MockConfigEntry

EVSE_PART_NUMBER_KEY = "evse_part_number"
DSM_STATE_KEY = "dsm_state"
SOFTWARE_VERSION_KEY = "software_version"

WIFI_SSID_TOPIC = "status/wifi-ssid"
POLICY_TOPIC = "shed/policy"
VENDOR_TOPIC = "info/vendor-name"
MODEL_TOPIC = "info/model"
HARDWARE_TOPIC = "info/hardware-version"
FIRMWARE_TOPIC = "info/firmware-version"
PART_NUMBER_TOPIC = "info/part-number"

EVSE = "evse"

# What the panel's device card showed before any of this was readable, and what
# a panel publishing nothing must go on showing.
FALLBACK_MANUFACTURER = "Span"
FALLBACK_MODEL = "SPAN Panel"


@pytest.fixture(autouse=True)
def _mock_entity_registry() -> Any:
    """Patch the entity-registry lookup sensor construction performs."""
    registry = MagicMock()
    registry.async_get_entity_id.return_value = None
    with patch(
        "custom_components.span_panel.sensor_base.er.async_get",
        return_value=registry,
    ):
        yield registry


def _published(device_id: str, topic: str) -> str:
    """What the capture publishes on one topic, or fail saying it does not."""
    value = schema_one_tree()[device_id].get(topic)
    assert value is not None, f"{device_id} publishes no {topic} in the capture"
    return value


def _snapshot(**rewrites: str | None) -> SpanPanelSnapshot:
    """A snapshot from the capture with panel topics rewritten or unpublished.

    Keyword spelling is `node__property_name`. `None` removes the topic, which is
    what a panel whose firmware omits a property looks like -- a different event
    from publishing an empty string, and the one the fallbacks exist for.
    """
    tree = schema_one_tree()
    for path, value in rewrites.items():
        node, _, prop = path.partition("__")
        topic = f"{node.replace('_', '-')}/{prop.replace('_', '-')}"
        if value is None:
            tree[SCHEMA_ONE_PANEL].pop(topic, None)
        else:
            tree[SCHEMA_ONE_PANEL][topic] = value
    return schema_one_snapshot(tree)


def _evse_snapshot(**rewrites: str | None) -> SpanPanelSnapshot:
    """The same, against the first EVSE in the capture."""
    tree = schema_one_tree()
    for path, value in rewrites.items():
        node, _, prop = path.partition("__")
        topic = f"{node.replace('_', '-')}/{prop.replace('_', '-')}"
        if value is None:
            tree[EVSE].pop(topic, None)
        else:
            tree[EVSE][topic] = value
    return schema_one_snapshot(tree)


def _coordinator(snapshot: SpanPanelSnapshot) -> MagicMock:
    """A coordinator-like mock carrying one snapshot."""
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.hass = MagicMock()
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    coordinator.unresolved_paths = frozenset()
    coordinator.config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.50"},
        options={},
        title="SPAN Panel",
        unique_id=snapshot.serial_number,
    )
    coordinator.config_entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator, panel_device_id="panel-device-id"
    )
    return coordinator


def _panel_sensors(snapshot: SpanPanelSnapshot) -> dict[str, Any]:
    """Every panel-level sensor the platform creates, keyed by description key."""
    coordinator = _coordinator(snapshot)
    created = create_panel_sensors(coordinator, snapshot, coordinator.config_entry)
    return {sensor.entity_description.key: sensor for sensor in created}


def _attributes(snapshot: SpanPanelSnapshot, key: str) -> dict[str, Any]:
    """The attributes one panel sensor reports, or an empty dict for none."""
    sensor = _panel_sensors(snapshot)[key]
    return sensor.extra_state_attributes or {}


async def _binary_sensors(hass: HomeAssistant, snapshot: SpanPanelSnapshot) -> dict[str, Any]:
    """Every binary sensor the platform creates, keyed by description key.

    Through `async_setup_entry` rather than by constructing an entity directly:
    which entity class serves which description is exactly what is under test
    here, and a direct construction would only assert the class the test itself
    picked.
    """
    coordinator = _coordinator(snapshot)
    added = MagicMock()
    await binary_sensor_async_setup_entry(hass, coordinator.config_entry, added)
    return {entity.entity_description.key: entity for entity in added.call_args.args[0]}


async def _registered_panel(
    hass: HomeAssistant, snapshot: SpanPanelSnapshot, entry_id: str
) -> dr.DeviceEntry:
    """Register the panel the way setup does, and read its card back.

    Through the registry rather than through `snapshot_to_device_info`'s return
    value: the dict is this integration's claim, the registry entry is the device
    a user opens. A field the registry drops or overwrites is invisible to an
    assertion on the dict.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.30"},
        entry_id=entry_id,
        unique_id=snapshot.serial_number,
    )
    entry.add_to_hass(hass)
    await ensure_device_registered(hass, entry, snapshot, "SPAN Panel")
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, snapshot.serial_number), entry.entry_id
    )
    assert device is not None
    return device


# ---------------------------------------------------------------------------
# The premise
# ---------------------------------------------------------------------------


def test_the_capture_publishes_everything_this_module_reads() -> None:
    """Guard the premise, since every expectation below is read from the capture.

    The capture was eight identity properties behind the producer until it was
    refreshed, and the shape of that gap is exactly what makes this guard worth
    having: a test whose expected value comes from an unpublished topic does not
    fail, it stops asserting anything.
    """
    assert _published(SCHEMA_ONE_PANEL, WIFI_SSID_TOPIC)
    assert _published(SCHEMA_ONE_PANEL, POLICY_TOPIC)
    assert _published(SCHEMA_ONE_PANEL, VENDOR_TOPIC)
    assert _published(SCHEMA_ONE_PANEL, MODEL_TOPIC)
    assert _published(SCHEMA_ONE_PANEL, HARDWARE_TOPIC)
    assert _published(EVSE, PART_NUMBER_TOPIC)


# ---------------------------------------------------------------------------
# The panel's device card
# ---------------------------------------------------------------------------


async def test_the_panel_card_shows_the_identity_the_panel_publishes(
    hass: HomeAssistant,
) -> None:
    """Manufacturer, model and hardware revision come off the wire, not a constant.

    Read back from the registry, and compared against the capture rather than
    against literals: the model in particular is `MAIN_40`, which is also the
    string `panel_size` is derived from, so an assertion written as a literal
    would agree with the size lookup rather than with the panel.
    """
    device = await _registered_panel(hass, _snapshot(), "entry-card-published")

    assert device.manufacturer == _published(SCHEMA_ONE_PANEL, VENDOR_TOPIC)
    assert device.model == _published(SCHEMA_ONE_PANEL, MODEL_TOPIC)
    assert device.hw_version == _published(SCHEMA_ONE_PANEL, HARDWARE_TOPIC)
    assert device.sw_version == _published(SCHEMA_ONE_PANEL, FIRMWARE_TOPIC)


async def test_the_panel_card_follows_a_republished_identity(hass: HomeAssistant) -> None:
    """The card tracks the wire, so nothing here is passing on a coincidence.

    `MAIN_32` is deliberately another real model: it keeps `panel_size` resolvable,
    so the only thing the rewrite changes is the string on the card.
    """
    rewritten = _snapshot(
        info__vendor_name="Another Vendor",
        info__model="MAIN_32",
        info__hardware_version="rev9",
    )

    device = await _registered_panel(hass, rewritten, "entry-card-rewritten")

    assert device.manufacturer == "Another Vendor"
    assert device.model == "MAIN_32"
    assert device.hw_version == "rev9"


async def test_a_panel_publishing_no_identity_keeps_the_card_it_has_always_had(
    hass: HomeAssistant,
) -> None:
    """The fallbacks are the compatibility guarantee, not a courtesy.

    Flat firmware declares none of these three, so every existing installation
    lands here. A panel that omits one must keep the row it has rather than
    losing it -- and `hw_version`, which never had a string to fall back to, must
    be absent rather than blank: `DeviceInfo` omits a `None` and renders an empty
    string as a present-but-empty row.
    """
    bare = _snapshot(info__vendor_name=None, info__model=None, info__hardware_version=None)

    device = await _registered_panel(hass, bare, "entry-card-bare")

    assert device.manufacturer == FALLBACK_MANUFACTURER
    assert device.model == FALLBACK_MODEL
    assert device.hw_version is None


def test_the_panel_identity_paths_are_enumerated_as_device_card_reads() -> None:
    """`snapshot_to_device_info` is not an entity, so its reads are exempt residuals.

    Annotated `NEITHER` beside the `mid.*` device-card reads: flat declares none
    of the three, and a schema_1 metadata row exists to carry a unit and a
    datatype for a reading, which an identity string is not. Asserted here so the
    three cannot quietly leave the inventory that is the only record of them.
    """
    for path in ("panel.vendor_name", "panel.model", "panel.hardware_version"):
        assert path in RESIDUAL_EXEMPT_PATHS, path
        assert path not in declared_field_paths(), path


# ---------------------------------------------------------------------------
# `panel.wifi_ssid` -- the flat -> v1.0 regression
# ---------------------------------------------------------------------------


def test_the_ssid_moved_off_the_software_version_sensor() -> None:
    """The old site, asserted absent — deliberately, and not coming back.

    A network name on a firmware-version sensor was incoherent; it only ever sat
    there because `panel_size` was already occupying the attribute block. The
    value is not lost, it moved: `test_the_wifi_link_sensor_carries_the_network_it_is_linked_to`
    reads it back out of this same capture on the Wi-Fi Link binary sensor.

    Asserted against a snapshot that *does* publish an SSID, so restoring the
    read fails here rather than passing on a panel that happens to carry none.
    """
    attributes = _attributes(_snapshot(), SOFTWARE_VERSION_KEY)

    assert _published(SCHEMA_ONE_PANEL, WIFI_SSID_TOPIC)
    assert "wifi_ssid" not in attributes
    # The attribute block did not collapse; only the SSID left it.
    assert "panel_size" in attributes


def test_the_ssid_is_a_declaration_now_rather_than_an_exemption() -> None:
    """The gate's own ratchet, asserted where a reader will find it.

    Both adapters map `wifi_ssid`, so the path satisfies the producible gate and
    `test_no_exempt_path_is_producible_by_both` refuses to let it stay exempt.
    It is declared on the entity that reads it -- one entity, now that the read
    has moved -- which is what lets a Repair name the entity a dead field takes
    with it.
    """
    assert "panel.wifi_ssid" not in RESIDUAL_EXEMPT_PATHS
    assert "panel.wifi_ssid" in declared_field_paths()
    assert "panel.wifi_ssid" in SpanPanelWifiLinkBinarySensor._residual_field_paths
    assert "panel.wifi_ssid" not in SpanPanelStatus._residual_field_paths


async def test_the_wifi_link_sensor_carries_the_network_it_is_linked_to(
    hass: HomeAssistant,
) -> None:
    """The coherent host: the link sensor says which network the link is to.

    Read out of the capture rather than compared against a literal, so what is
    under test is the whole route -- published topic, mapper, snapshot field,
    attribute -- and not a constant the code also holds.
    """
    sensors = await _binary_sensors(hass, _snapshot())

    attributes = sensors[SYSTEM_WIFI_LINK].extra_state_attributes

    assert attributes == {"wifi_ssid": _published(SCHEMA_ONE_PANEL, WIFI_SSID_TOPIC)}


async def test_the_wifi_link_attribute_follows_a_republished_ssid(
    hass: HomeAssistant,
) -> None:
    """A panel that joins another network says so, which a hardcoded value never could."""
    sensors = await _binary_sensors(hass, _snapshot(status__wifi_ssid="another-network"))

    assert sensors[SYSTEM_WIFI_LINK].extra_state_attributes == {"wifi_ssid": "another-network"}


async def test_an_unpublished_ssid_leaves_the_wifi_link_attribute_off_entirely(
    hass: HomeAssistant,
) -> None:
    """Absent, not `None`. A present-but-empty attribute reads as a failed reading."""
    sensors = await _binary_sensors(hass, _snapshot(status__wifi_ssid=None))

    assert sensors[SYSTEM_WIFI_LINK].extra_state_attributes is None


async def test_only_the_wifi_link_sensor_declares_the_ssid_it_reads(
    hass: HomeAssistant,
) -> None:
    """The reason the Wi-Fi link gets an entity class of its own.

    `_residual_field_paths` is a `ClassVar` and one class serves every panel
    binary sensor, so declaring the SSID on that base class would claim the door
    sensor reads it. That is not cosmetic: the declaration is what a Repair
    consults to name the entities a dead field took down with it, so an
    unresolved `panel.wifi_ssid` would name the door.
    """
    sensors = await _binary_sensors(hass, _snapshot())
    wifi_link = sensors[SYSTEM_WIFI_LINK]
    door = sensors[SYSTEM_DOOR_STATE]

    assert "panel.wifi_ssid" in type(wifi_link)._residual_field_paths
    assert "panel.wifi_ssid" in wifi_link._declared_field_paths()

    assert "panel.wifi_ssid" not in type(door)._residual_field_paths
    assert "panel.wifi_ssid" not in door._declared_field_paths()


# ---------------------------------------------------------------------------
# `shed/policy` -- attributes on `dsm_state`
# ---------------------------------------------------------------------------


def test_the_shed_policy_reaches_dsm_state_as_its_two_thresholds() -> None:
    """The numbers that make the panel's shed behaviour predictable.

    Compared against the document the capture publishes rather than against
    literals, so the parse is checked against the producer's own encoding of it.
    """
    document = json.loads(_published(SCHEMA_ONE_PANEL, POLICY_TOPIC))
    attributes = _attributes(_snapshot(), DSM_STATE_KEY)

    assert attributes["shed_algorithm"] == document["algorithm"]
    assert attributes["soc_threshold_shed"] == document["parameters"]["soc-threshold-shed"]
    assert attributes["soc_threshold_release"] == document["parameters"]["soc-threshold-release"]
    # Fully parsed, so the raw document adds nothing a user could act on.
    assert "shed_policy" not in attributes


def test_the_thresholds_follow_a_republished_policy() -> None:
    """A panel reconfigured to shed later says so."""
    rewritten = json.dumps(
        {
            "algorithm": "soc-priority.v1",
            "parameters": {"soc-threshold-shed": 5, "soc-threshold-release": 15},
        }
    )

    attributes = _attributes(_snapshot(shed__policy=rewritten), DSM_STATE_KEY)

    assert attributes["soc_threshold_shed"] == 5
    assert attributes["soc_threshold_release"] == 15


def test_an_unknown_algorithm_degrades_to_the_raw_document() -> None:
    """The policy schema is versioned in its own `$id`, so another algorithm may arrive.

    Reporting `soc-priority.v1`'s thresholds for a document that never had them
    would be worse than reporting nothing, and raising would take the sensor
    down. Naming the algorithm and showing the document is what a user can act on.
    """
    other = json.dumps({"algorithm": "runtime-priority.v2", "parameters": {"minutes-shed": 30}})

    attributes = _attributes(_snapshot(shed__policy=other), DSM_STATE_KEY)

    assert attributes["shed_algorithm"] == "runtime-priority.v2"
    assert attributes["shed_policy"] == other
    assert "soc_threshold_shed" not in attributes
    assert "soc_threshold_release" not in attributes


def test_an_unparseable_policy_still_leaves_the_sensor_standing() -> None:
    """A panel is a publisher this integration does not control.

    One malformed string must not take `dsm_state` -- the sensor a button reads
    to decide whether the panel is already on grid -- down with it.
    """
    sensor = _panel_sensors(_snapshot(shed__policy="{not json"))[DSM_STATE_KEY]
    sensor._update_native_value()

    assert sensor.native_value is not None
    assert (sensor.extra_state_attributes or {}) == {"shed_policy": "{not json"}


def test_a_panel_publishing_no_policy_reports_no_policy_attributes() -> None:
    """`dsm_state` keeps its state and simply carries nothing extra."""
    sensor = _panel_sensors(_snapshot(shed__policy=None))[DSM_STATE_KEY]
    sensor._update_native_value()

    assert sensor.native_value is not None
    assert sensor.extra_state_attributes is None


def test_the_policy_attributes_hang_off_dsm_state_and_nothing_else() -> None:
    """One sensor's attributes, not every sensor this class renders.

    `SpanPanelPanelStatus` renders the relay state and the run config too, and a
    shed policy repeated on each of them is noise on three cards.
    """
    sensors = _panel_sensors(_snapshot())
    carrying = {
        key
        for key, sensor in sensors.items()
        if isinstance(sensor, SpanPanelPanelStatus)
        and "shed_algorithm" in (sensor.extra_state_attributes or {})
    }

    assert carrying == {DSM_STATE_KEY}


# ---------------------------------------------------------------------------
# `evse.part_number` -- the promotion the producible gate demanded
# ---------------------------------------------------------------------------


def _evse_states(snapshot: SpanPanelSnapshot, key: str) -> set[Any]:
    """What every charger's sensor of this key reports."""
    states: set[Any] = set()
    for sensor in create_evse_sensors(_coordinator(snapshot), snapshot):
        if sensor.entity_description.key != key:
            continue
        sensor._update_native_value()
        states.add(sensor.native_value)
    return states


def test_the_charger_reports_the_part_number_the_panel_publishes() -> None:
    """The BESS has shown its SKU since it shipped; the charger beside it had none."""
    assert _evse_states(_snapshot(), EVSE_PART_NUMBER_KEY) == {_published(EVSE, PART_NUMBER_TOPIC)}


def test_the_part_number_follows_a_republished_value() -> None:
    """Two chargers, one rewritten, so the sensor cannot be reading a constant.

    The capture publishes the same SKU on both, which is what makes this the
    mutation worth running: a rewrite of one has to show up as two distinct
    states rather than as one.
    """
    states = _evse_states(_evse_snapshot(info__part_number="SPN-DRV-999"), EVSE_PART_NUMBER_KEY)

    assert states == {"SPN-DRV-999", _published(EVSE, PART_NUMBER_TOPIC)}


def test_a_charger_publishing_no_part_number_reports_unknown_rather_than_a_default() -> None:
    """An unpublished SKU is unknown, and no charger invents one for another.

    `STATE_UNKNOWN` rather than `None` because the platform renders a
    non-numeric sensor's absent value that way, which is how `bess_part_number`
    has always behaved on a BESS that publishes none. The other charger keeps
    its value in the same breath, so this is one charger going quiet rather than
    the sensor failing.
    """
    states = _evse_states(_evse_snapshot(info__part_number=None), EVSE_PART_NUMBER_KEY)

    assert states == {STATE_UNKNOWN, _published(EVSE, PART_NUMBER_TOPIC)}


def test_the_part_number_is_a_plain_declaration_on_both_adapters() -> None:
    """The promotion this task's schema_1 metadata row demanded.

    Flat has mapped `evse/part-number` all along; adding the v1.0 row made the
    path producible by both, and a both-producible path is a declaration rather
    than an exemption. Diagnostic and off by default, matching `bess_part_number`
    -- build metadata is not something a user wants on a card by default.
    """
    (description,) = [d for d in EVSE_SENSORS if d.key == EVSE_PART_NUMBER_KEY]

    assert description.field_path == "evse.part_number"
    assert description.derived is None
    assert description.entity_registry_enabled_default is False
    assert "evse.part_number" in declared_field_paths()
    assert "evse.part_number" not in RESIDUAL_EXEMPT_PATHS
